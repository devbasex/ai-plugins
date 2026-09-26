"""supervise.py new の宣言の扱い（#1192）・設計のプランの入口（#1193）・種別ごとの help（#1194）。"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SUPERVISE = SCRIPTS / "supervise.py"
PY = sys.executable

spec = importlib.util.spec_from_file_location("supervise_new", SUPERVISE)
sv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sv)


def cli(*args, cwd):
    return subprocess.run([PY, str(SUPERVISE), *args], capture_output=True, text=True, cwd=cwd)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def plain_repo(tmp_path, release=None):
    """main へのマージが配る形のリポジトリ（プラグインでない）を模す。"""
    root = tmp_path / "other"
    (root / ".ndf").mkdir(parents=True)
    (root / ".ndf" / "worktree.json").write_text('{"version": 1, "base_branch": "main"}\n')
    decl = {"version": 1, "test": {"command": "make test {paths}"}}
    if release is not None:
        decl["release"] = release
    (root / ".ndf" / "supervise.json").write_text(json.dumps(decl))
    return root


def new_mission(root, out, *extra):
    return cli("new", "mission", "--name", "m6", "--worktree", str(root), "--issue", "216", "247",
               "--design", "216", "--version", "3.8.0-dev.1", "--out", str(out), *extra, cwd=root)


# --- #1192: リリースの雛形の無い形 -------------------------------------------------

@pytest.mark.parametrize("release", [None, {"form": "merge"}])
def test_new_mission_without_release_template_writes_until_check(tmp_path, release):
    root = plain_repo(tmp_path, release)
    out = tmp_path / "m"
    p = new_mission(root, out)
    assert p.returncode == 0, p.stderr
    res = json.loads(p.stdout)
    assert res["status"] == "ok" and "/ndf:release" in res["next"]
    manifest = json.loads((out / "mission.json").read_text())
    names = [w["name"] for w in manifest["ステージ"]]
    assert names == ["設計", "関門 1", "ミッションのブランチ", "実装", "検査", "リリース"]
    last = manifest["ステージ"][-1]
    assert last["manual"] == "/ndf:release" and "plans" not in last and "command" not in last
    assert any(it.get("manual") == "/ndf:release" for it in res["items"])
    waves = {w["name"]: w for w in manifest["ステージ"]}
    assert "--then" not in waves["検査"]["command"]  # 検査の queue の後に流す計画は無い


def test_new_mission_with_package_plugin_keeps_release_stage(tmp_path):
    root = plain_repo(tmp_path, {"form": "package-plugin", "plugin": "foo", "runtimes": ["claude"]})
    out = tmp_path / "m"
    p = new_mission(root, out)
    assert p.returncode == 0, p.stderr
    names = [w["name"] for w in json.loads((out / "mission.json").read_text())["ステージ"]]
    assert names[-1] == "配布" and "/ndf:release" not in json.loads(p.stdout)["next"]


def test_new_close_still_needs_release_form(tmp_path):
    root = plain_repo(tmp_path)
    p = cli("new", "close", "--name", "m6", "--worktree", str(root), "--issue", "216", "--version", "3.8.0-dev.1",
            "--prod", "3.8.0", "--state", str(tmp_path / "s.json"), "--out", str(tmp_path / "c"), cwd=root)
    assert p.returncode == 2 and "release.form" in p.stderr


# --- #1193: 設計のプランの入口 --------------------------------------------------------

def design_plan(tmp_path):
    root = plain_repo(tmp_path)
    out = tmp_path / "m"
    assert new_mission(root, out).returncode == 0
    waves = {w["name"]: w for w in json.loads((out / "mission.json").read_text())["ステージ"]}
    return json.loads(Path(waves["設計"]["plans"][0]).read_text())


def test_design_plan_sets_up_glossary_and_requirements_before_design(tmp_path):
    plan = design_plan(tmp_path)
    ids = [s["id"] for s in plan["steps"]]
    assert ids[:4] == ["glossary", "requirements-check", "requirements", "design"]
    st = {s["id"]: s for s in plan["steps"]}
    assert "design-glossary" in st["glossary"]["cmd"] and "{state_dir}" in st["glossary"]["cmd"]
    assert st["glossary"]["next"] == "requirements-check"
    chk = st["requirements-check"]
    assert "spec-copy.py check 216 issues/issue-216-requirements.md" in chk["cmd"]
    assert chk["next"] == "design" and chk["on_fail"] == "requirements"  # 本文にあれば要求を飛ばす
    req = st["requirements"]
    assert req["type"] == "work" and req["full"] and "/ndf:requirements-design #216" in req["prompt"]
    assert req["next"] == "design"
    assert [s["id"] for s in plan["steps"] if s["type"] == "judge"] == ["gate"]  # 承認ゲートを増やさない
    assert any("glossary-candidates" in f for f in st["pr"]["append"])


def glossary_repo(tmp_path):
    root = tmp_path / "wt"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(root, "config", k, v)
    (root / "README.md").write_text("# r\n\n| 語 | 意味 |\n| --- | --- |\n| ステージ | 段 |\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def test_design_glossary_initializes_and_commits_when_missing(tmp_path):
    root = glossary_repo(tmp_path)
    out = tmp_path / "state" / "work" / "glossary-candidates.md"
    p = cli("design-glossary", "--mode", "standard", "--root", ".", "--out", str(out), cwd=root)
    assert p.returncode == 0, p.stdout + p.stderr
    res = json.loads(p.stdout.strip().splitlines()[-1])
    assert res["status"] == "ok" and res["metrics"]["initialized"] == 1
    assert (root / ".ndf" / "glossary.json").is_file()
    assert git(root, "status", "--porcelain") == ""  # 起こした用語集はコミットした
    assert ".ndf/glossary.json" in git(root, "show", "--name-only", "--format=", "HEAD")
    assert out.is_file() and "承認ゲート 1" in out.read_text()
    # 揃った後は何もしない
    head = git(root, "rev-parse", "HEAD")
    out.unlink()
    p = cli("design-glossary", "--mode", "standard", "--root", ".", "--out", str(out), cwd=root)
    assert p.returncode == 0 and json.loads(p.stdout.strip().splitlines()[-1])["metrics"]["initialized"] == 0
    assert git(root, "rev-parse", "HEAD") == head and not out.exists()


def test_pr_step_appends_existing_files(tmp_path, monkeypatch):
    root = glossary_repo(tmp_path)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    git(root, "remote", "add", "origin", str(origin))
    git(root, "push", "-q", "-u", "origin", "main")
    git(root, "checkout", "-q", "-b", "design/x")
    (root / "d.md").write_text("d\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Add: d")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(f"#!{PY}\nimport os, sys\na = sys.argv[1:]\n"
                               "if a[:2] == ['pr', 'create']:\n"
                               "    open(os.environ['FAKE_GH_BODY'], 'w').write(a[a.index('--body') + 1])\n"
                               "    print('https://github.com/o/r/pull/5')\n"
                               "sys.exit(0)\n")
    (bindir / "gh").chmod(0o755)
    body = tmp_path / "body.txt"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_BODY", str(body))
    state = tmp_path / "state"
    (state / "work").mkdir(parents=True)
    (state / "work" / "cand.md").write_text("## 用語集の候補\n\n- 目印の語\n")
    plan = {"フェーズ": "設計", "課題": [1], "作業場所": str(root), "steps": [
        {"id": "pr", "type": "pr", "base": "main", "body": "template", "next": "end",
         "append": ["{state_dir}/work/cand.md", "{state_dir}/work/none.md"]}]}
    assert "結果: 完了" in sv.Supervisor(plan, state).run()
    got = body.read_text()
    assert "目印の語" in got and got.index("目印の語") < got.index(sv.PR_FOOTER)


# --- #1194: 種別ごとの help ------------------------------------------------------------

def test_new_mission_help_shows_only_its_arguments_and_declarations(tmp_path):
    p = cli("new", "mission", "--help", cwd=tmp_path)
    assert p.returncode == 0
    for word in ("--design", "--version", "base_branch", "test.command", "release.form", "/ndf:release"):
        assert word in p.stdout, word
    for word in ("--prs", "--channel", "--title", "--since-last"):
        assert word not in p.stdout, word


def test_new_impl_help_differs_from_mission(tmp_path):
    impl = cli("new", "impl", "--help", cwd=tmp_path).stdout
    assert "--tests" in impl and "--test-cmd" in impl and "--design" not in impl and "--channel" not in impl


def test_every_new_argument_has_help(tmp_path):
    for kind in ("impl", "check", "release", "mission", "close"):
        p = cli("new", kind, "--help", cwd=tmp_path)
        assert p.returncode == 0, kind
        opts = p.stdout.split("options:\n", 1)[1].split("\n\n", 1)[0].splitlines()
        for i, line in enumerate(opts):
            s = line.strip()
            if s.startswith("--") and "  " not in s:
                # 引数の名前だけの行は、次の行に説明が続く
                nxt = opts[i + 1].strip() if i + 1 < len(opts) else ""
                assert nxt and not nxt.startswith("-"), (kind, s)


def test_top_help_has_phase_table(tmp_path):
    out = cli("--help", cwd=tmp_path).stdout
    for kind in ("new mission", "new impl", "new check", "new release", "new close"):
        assert kind in out, kind
    for sub in ("run", "queue", "wait"):
        assert cli(sub, "--help", cwd=tmp_path).stdout.count("\n") > 5, sub
