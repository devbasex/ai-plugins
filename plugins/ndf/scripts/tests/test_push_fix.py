"""実装のプランの pr のステップが push 前の検査で拒まれたとき、修正の worker が直して打ち直す（#1767 の受け入れ条件 6〜10）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SUPERVISE = SCRIPTS / "supervise.py"
REPO = SCRIPTS.parents[2]
PY = sys.executable

sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
from supervise_lib import engine, plan as plan_mod, pr as pr_step, procedures  # noqa: E402

# bad というファイルがあれば落ちる pre-push フック。指摘は標準出力へ書く（決定 8）
HOOK = "#!/bin/sh\nif git ls-files --error-unmatch bad >/dev/null 2>&1; then echo 'ERROR: bad が残っている（目印の検査）'; exit 1; fi\n"

# 修正の worker の偽物。FAKE_FIX=1 なら bad を消してコミットする
FAKE_CLAUDE = """#!{py}
import json, os, subprocess, sys
open(os.environ["FAKE_CLAUDE_LOG"], "a").write(sys.stdin.read() + "\\n=====\\n")
if os.environ.get("FAKE_FIX") == "1" and os.path.exists("bad"):
    subprocess.run(["git", "rm", "-q", "bad"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fix: bad を消す"], check=True)
print(json.dumps({{"result": "## 作業の報告\\n- 結果: 完了", "usage": {{}}, "total_cost_usd": 0.01, "num_turns": 1}}))
"""

FAKE_GH = """#!{py}
import sys
a = sys.argv[1:]
if a[:2] == ["pr", "create"]:
    print("https://github.com/o/r/pull/5")
sys.exit(0)
"""


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """bad を持つコミットが HEAD の作業場所。origin は裸のリポジトリで、pre-push フックが bad を拒む。"""
    root = tmp_path / "wt"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "a.txt").write_text("a\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    git(root, "remote", "add", "origin", str(origin))
    git(root, "push", "-q", "-u", "origin", "main")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "pre-push").write_text(HOOK)
    (hooks / "pre-push").chmod(0o755)
    git(root, "config", "core.hooksPath", str(hooks))
    git(root, "checkout", "-q", "-b", "feat/x")
    (root / "bad").write_text("x\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Add: bad")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("gh", FAKE_GH), ("claude", FAKE_CLAUDE)):
        (bindir / name).write_text(body.format(py=PY))
        (bindir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude.log"))
    monkeypatch.delenv("NDF_SUPERVISE_CLAUDE", raising=False)
    return root


def merge_plan(root: Path, pr: dict | None = None) -> dict:
    """plan_to_merge の pr・fix-push・test-all の並びだけを持つプラン。"""
    step = pr or {"id": "pr", "type": "pr", "base": "main", "body": "template", "title": "T", "next": "test-all"}
    fix = procedures.push_fix_steps(step) if pr is None else []
    steps = [step, *fix, {"id": "test-all", "type": "run", "cmd": "true", "next": "end"}]
    return {"フェーズ": "実装", "課題": [5], "作業場所": str(root), "上限": 20, "steps": steps}


def ran(e: engine.Engine) -> list[str]:
    return [x["id"] for x in e.state.log]


# ---------- 受け入れ条件 6・決定 8 ----------


def test_a_push_check_failure_is_fixed_and_the_pr_step_runs_again(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FIX", "1")
    e = engine.Engine(merge_plan(repo), tmp_path / "state")
    text = e.run()
    assert "結果: 完了" in text, text
    assert ran(e) == ["pr", "fix-push", "pr", "test-all"]
    # フックが標準出力へ書いた指摘が修正の worker の入力に載る
    prompt = (tmp_path / "claude.log").read_text()
    assert "ERROR: bad が残っている" in prompt and "push 前の検査" in prompt
    assert git(repo, "ls-remote", "origin", "feat/x").split()[0] == git(repo, "rev-parse", "HEAD").strip()


# ---------- 受け入れ条件 7・I4 ----------


def test_the_same_commit_failing_twice_stops_with_the_last_push_output(repo, tmp_path):
    state = tmp_path / "state"
    e = engine.Engine(merge_plan(repo), state)
    text = e.run()
    assert "結果: 止まった" in text
    assert ran(e) == ["pr", "fix-push", "pr"]
    last = e.state.results["pr"]
    assert last["push_check"] is False and "ERROR: bad が残っている" in last["text"] and "2 回続けて" in last["text"]
    outs = sorted(state.glob("*-pr.out"))
    assert len(outs) == 2 and "ERROR: bad が残っている" in outs[-1].read_text()


# ---------- I3・決定 3 ----------


def test_a_failure_that_a_commit_cannot_fix_does_not_reach_the_worker(repo, tmp_path):
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    e = engine.Engine(merge_plan(repo), tmp_path / "state")
    assert "結果: 止まった" in e.run()
    assert ran(e) == ["pr"] and e.state.results["pr"]["push_check"] is False
    assert not (tmp_path / "claude.log").exists()


@pytest.mark.parametrize(
    ("code", "output", "check"),
    [
        (1, "ERROR: hook says no\nerror: failed to push some refs to '../remote.git'\n", True),
        # 資格情報の補助の fatal: がフックの前に出ても、push 前の検査の不合格と読む（m744 の 06-pr.out）
        (1, "fatal: failed to get: -25308\nERROR: 食い違う\nerror: failed to push some refs\n", True),
        (1, " ! [rejected]        HEAD -> master (fetch first)\nerror: failed to push some refs\nhint: x\n", False),
        (1, " ! [remote rejected] HEAD -> main (protected branch hook declined)\n", False),
        (128, "fatal: '/nonexist/x.git' does not appear to be a git repository\n", False),
    ],
)
def test_push_failures_are_told_apart_by_the_exit_code_and_the_rejected_line(code, output, check):
    assert pr_step.is_push_check_failure(code, output) is check


# ---------- 決定 4 ----------


def test_on_fail_without_on_fail_only_still_takes_every_failure(repo, tmp_path):
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    pr = {"id": "pr", "type": "pr", "base": "main", "body": "template", "title": "T", "on_fail": "after", "next": "test-all"}
    plan = merge_plan(repo, pr)
    plan["steps"].insert(1, {"id": "after", "type": "run", "cmd": "true", "next": "end"})
    e = engine.Engine(plan, tmp_path / "state")
    assert "結果: 完了" in e.run()
    assert ran(e) == ["pr", "after"]
    assert plan_mod.fail_kind_matches({"on_fail": "x"}, {}) and not plan_mod.fail_kind_matches({"on_fail_only": "push-check"}, {})


# ---------- 受け入れ条件 6・8（雛形） ----------


def new_plan(tmp_path: Path, kind: str, decl: dict, *extra: str) -> dict:
    repo = tmp_path / "r"
    (repo / ".ndf").mkdir(parents=True)
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": "main"}))
    (repo / ".ndf" / "supervise.json").write_text(json.dumps(decl))
    out = tmp_path / f"{kind}.json"
    p = subprocess.run([PY, str(SUPERVISE), "new", kind, *extra, "--out", str(out)], capture_output=True, text=True, cwd=repo)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(out.read_text())


@pytest.mark.parametrize("sync", [True, False])
@pytest.mark.parametrize("kind", ["impl", "fix"])
def test_merge_plans_have_the_push_fix_with_or_without_sync_checks(tmp_path, kind, sync):
    decl = {"version": 1, "test": {"command": "make test {paths}"}}
    if sync:
        decl["sync_checks"] = [{"name": "x", "command": "true"}]
    wt = str(tmp_path / "wt")
    extra = ["--issue", "5", "--worktree", wt, "--tests", "t", "--title", "T"]
    steps = {s["id"]: s for s in new_plan(tmp_path, kind, decl, *extra)["steps"]}
    assert ("sync" in steps) is sync
    assert steps["pr"]["on_fail"] == "fix-push" and steps["pr"]["on_fail_only"] == "push-check"
    assert steps["fix-push"]["inputs"] == ["pr"] and steps["fix-push"]["next"] == "pr" and steps["pr"]["next"] == "test-all"


# ---------- 受け入れ条件 9・10 ----------


def test_the_templates_do_not_name_this_repositorys_checks():
    for f in (SCRIPTS / "supervise_lib").glob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert "validate-runtime-plugins.sh" not in text and "check-lint.sh" not in text, f.name


def test_this_repository_syncs_the_pre_push_checks():
    commands = [c["command"] for c in json.loads((REPO / ".ndf" / "supervise.json").read_text())["sync_checks"]]
    assert "bash scripts/validate-runtime-plugins.sh" in commands and "bash scripts/check-lint.sh" in commands
