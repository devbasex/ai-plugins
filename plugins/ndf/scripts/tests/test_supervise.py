"""supervise.py の段（run の定型・再実行・飛ばす遷移・課題の本文）と副命令（new / queue / note / sync-check）。

gh と claude は PATH の先頭に置いた偽物で置き換える。実機の claude は起動しない。
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
SUPERVISE = SCRIPTS / "supervise.py"
PY = sys.executable

sys.path.insert(0, str(SCRIPTS / "lib"))
from step_result import validate_result  # noqa: E402

spec = importlib.util.spec_from_file_location("supervise", SUPERVISE)
sv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sv)

FAKE_GH = """#!{py}
import json, sys
a = sys.argv[1:]
if a[:2] == ["issue", "view"]:
    print(json.dumps({{"title": "題 " + a[2], "body": "本文の印 " + a[2]}}))
    sys.exit(0)
sys.exit(1)
"""

FAKE_CLAUDE = """#!{py}
import json, os, sys
open(os.environ["FAKE_CLAUDE_LOG"], "a").write(sys.stdin.read() + "\\n=====\\n")
print(json.dumps({{"result": "## 作業の報告\\n- 結果: 完了", "usage": {{}}, "total_cost_usd": 0.01,
                  "num_turns": 1}}))
"""


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("gh", FAKE_GH), ("claude", FAKE_CLAUDE)):
        f = bindir / name
        f.write_text(body.format(py=PY))
        f.chmod(0o755)
    log = tmp_path / "claude.log"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.delenv("NDF_SUPERVISE_CLAUDE", raising=False)
    return log


def run_plan(tmp_path, steps, **extra):
    plan = {"持ち場": "試験", "課題": [858], "作業場所": str(tmp_path), "steps": steps, **extra}
    s = sv.Supervisor(plan, tmp_path / "state")
    text = s.run()
    return s, text


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def test_work_puts_issue_body_into_prompt(tmp_path, fakes):
    s, text = run_plan(tmp_path, [{"id": "impl", "type": "work", "issues": True, "prompt": "実装する",
                                   "next": "end"}])
    assert "結果: 完了" in text
    prompt = fakes.read_text()
    assert "## 課題 #858: 題 858" in prompt and "本文の印 858" in prompt
    assert prompt.index("本文の印") < prompt.index("実装する")


def test_work_without_issues_does_not_call_gh(tmp_path, fakes):
    run_plan(tmp_path, [{"id": "impl", "type": "work", "prompt": "実装する", "next": "end"}])
    assert "## 課題" not in fakes.read_text()


def test_rerun_failed_passes_when_last_failed_run_passes(tmp_path):
    # 1 回目は落ち、PYTEST_ADDOPTS に --lf が付いた 2 回目は通る
    cmd = 'case "$PYTEST_ADDOPTS" in *--lf*) exit 0;; *) echo boom; exit 1;; esac'
    s, text = run_plan(tmp_path, [{"id": "t", "type": "run", "cmd": cmd, "rerun_failed": True, "next": "end"}])
    assert "結果: 完了" in text
    assert s.results["t"]["rerun"] == {"exit": 0}
    assert "揺れとして進む" in s.results["t"]["text"]


def test_rerun_failed_still_failing_goes_to_on_fail(tmp_path):
    steps = [{"id": "t", "type": "run", "cmd": "exit 1", "rerun_failed": True, "on_fail": "after"},
             {"id": "after", "type": "run", "cmd": "true", "next": "end"}]
    s, text = run_plan(tmp_path, steps)
    assert s.results["t"]["rerun"] == {"exit": 1}
    assert [e["id"] for e in s.log] == ["t", "after"]


def test_run_step_disables_pytest_reports(tmp_path):
    s, _ = run_plan(tmp_path, [{"id": "t", "type": "run", "cmd": 'echo "[$PYTEST_ADDOPTS]"', "next": "end"}])
    assert "-p no:playwright-kit" in s.results["t"]["text"]
    s, _ = run_plan(tmp_path, [{"id": "t", "type": "run", "cmd": 'echo "[$PYTEST_ADDOPTS]"', "reports": True,
                                "next": "end"}])
    assert "no:playwright-kit" not in s.results["t"]["text"]


def test_skip_to_jumps_over_steps(tmp_path):
    steps = [{"id": "assess", "type": "run", "cmd": "exit 3", "skip_to": "review", "on_fail": "refactor"},
             {"id": "refactor", "type": "run", "cmd": "true"},
             {"id": "review", "type": "run", "cmd": "true", "next": "end"}]
    s, text = run_plan(tmp_path, steps)
    assert "結果: 完了" in text
    assert [e["id"] for e in s.log] == ["assess", "review"]


def test_skip_to_passes_through_on_zero(tmp_path):
    steps = [{"id": "assess", "type": "run", "cmd": "true", "skip_to": "review"},
             {"id": "refactor", "type": "run", "cmd": "true"},
             {"id": "review", "type": "run", "cmd": "true", "next": "end"}]
    s, _ = run_plan(tmp_path, steps)
    assert [e["id"] for e in s.log] == ["assess", "refactor", "review"]


def test_preset_and_pr_placeholder(tmp_path, monkeypatch):
    monkeypatch.setitem(sv.PRESETS, "echo", "echo preset-ran")
    s, _ = run_plan(tmp_path, [{"id": "a", "type": "run", "preset": "echo"},
                               {"id": "b", "type": "run", "cmd": "echo pr={pr}", "next": "end"}],
                    **{"Pull Request": "https://example/pull/9"})
    assert "preset-ran" in s.results["a"]["text"]
    assert "pr=https://example/pull/9" in s.results["b"]["text"]


def test_pr_placeholder_without_pr_fails(tmp_path):
    _, text = run_plan(tmp_path, [{"id": "b", "type": "run", "cmd": "echo {pr}", "next": "end"}])
    assert "結果: 止まった" in text


def test_branch_creates_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "init")
    wt = repo / ".worktrees" / "feat" / "x"
    plan = {"持ち場": "試験", "課題": [], "作業場所": str(wt), "branch": "feat/x", "起点": "main",
            "steps": [{"id": "t", "type": "run", "cmd": "git rev-parse --abbrev-ref HEAD", "next": "end"}]}
    s = sv.Supervisor(plan, tmp_path / "state")
    assert "結果: 完了" in s.run()
    assert s.results["t"]["text"].strip() == "feat/x"


def test_branch_without_repo_stops(tmp_path):
    plan = {"持ち場": "試験", "課題": [], "作業場所": str(tmp_path / "nowhere"), "branch": "feat/x",
            "steps": [{"id": "t", "type": "run", "cmd": "true"}]}
    assert "リポジトリ" in sv.Supervisor(plan, tmp_path / "state").run()


def cli(*args, cwd=None):
    return subprocess.run([PY, str(SUPERVISE), *args], capture_output=True, text=True, cwd=cwd)


def test_new_impl_writes_plan(tmp_path):
    out = tmp_path / "plan.json"
    p = cli("new", "impl", "--issue", "858", "--worktree", "/w", "--tests", "plugins/ndf/scripts/tests",
            "--title", "Add: x（#858）", "--prompt", "指示", "--branch", "feat/issue-858-x", "--out", str(out))
    assert p.returncode == 0, p.stderr
    res = json.loads(p.stdout)
    assert res["status"] == "ok"
    plan = json.loads(out.read_text())
    ids = [s["id"] for s in plan["steps"]]
    assert ids[0] == "impl" and ids.index("test-limited") < ids.index("test-all") < ids.index("pr") < ids.index("merge")
    steps = {s["id"]: s for s in plan["steps"]}
    assert steps["impl"]["issues"] is True and steps["impl"]["prompt"] == "指示"
    assert steps["sync"]["preset"] == "sync-check"
    assert "plugins/ndf/scripts/tests" in steps["test-limited"]["cmd"] and steps["test-limited"]["rerun_failed"]
    assert plan["branch"] == "feat/issue-858-x"
    # 段の遷移がすべて知っている段を指す
    for s in plan["steps"]:
        for k in ("next", "on_fail", "skip_to"):
            if k in s:
                assert s[k] in steps or s[k] == "end"
        for c in s.get("choices", []):
            assert c in steps or c == "stop"


def test_new_check_uses_assess_skip(tmp_path):
    out = tmp_path / "plan.json"
    p = cli("new", "check", "--pr", "999", "--worktree", "/w", "--scope", "a.py", "--out", str(out))
    assert p.returncode == 0, p.stderr
    steps = {s["id"]: s for s in json.loads(out.read_text())["steps"]}
    assert steps["assess"]["skip_to"] == "review"
    assert "--scope a.py" in steps["refactor"]["args"]


def test_new_impl_requires_title():
    p = cli("new", "impl", "--issue", "1", "--worktree", "/w", "--tests", "t")
    assert p.returncode == 2


def test_queue_limits_concurrency(tmp_path):
    plans = []
    for i in range(3):
        f = tmp_path / f"p{i}.json"
        f.write_text(json.dumps({"持ち場": "試験", "課題": [], "作業場所": str(tmp_path), "steps": [
            {"id": "t", "type": "run", "cmd": f"date +%s.%N > {tmp_path}/s{i}; sleep 0.6; "
                                             f"date +%s.%N > {tmp_path}/e{i}", "next": "end"}]}))
        plans.append(str(f))
    p = cli("queue", *plans, "--max", "2", "--poll", "0.1")
    assert p.returncode == 0, p.stdout + p.stderr
    res = json.loads(p.stdout)
    assert res["status"] == "ok" and len(res["items"]) == 3
    assert all(i["result"] == "完了" for i in res["items"])
    t = {k: float((tmp_path / k).read_text()) for k in ("s0", "s1", "s2", "e0", "e1")}
    # 3 本目は最初の 2 本のどちらかが終わってから始まる
    assert t["s2"] >= min(t["e0"], t["e1"])


def test_queue_reports_stopped(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text(json.dumps({"持ち場": "試験", "課題": [], "作業場所": str(tmp_path),
                             "steps": [{"id": "t", "type": "run", "cmd": "exit 1"}]}))
    p = cli("queue", str(f), "--poll", "0.1")
    assert p.returncode == 1
    assert json.loads(p.stdout)["items"][0]["result"] == "止まった"


HANDOFF = """# 引継ぎ

## 今の会話の進み（再開するときはここから読む）

| まとまり | 状態 | 次 |
| --- | --- | --- |
| #1 | 済み | — |

後の段落

## 前の会話の進み

| まとまり | 状態 | 次 |
| --- | --- | --- |
"""


def test_note_appends_row(tmp_path):
    doc = tmp_path / "handoff.md"
    doc.write_text(HANDOFF)
    s, text = run_plan(tmp_path, [{"id": "t", "type": "run", "cmd": "true", "next": "end"}],
                       **{"Pull Request": "https://example/pull/9"})
    p = cli("note", str(doc), "--report", str(tmp_path / "state" / "report.md"), "--next", "マージ")
    assert p.returncode == 0, p.stdout + p.stderr
    lines = doc.read_text().splitlines()
    i = lines.index("| #1 | 済み | — |")
    assert lines[i + 1] == "| #858 | 試験: 完了（https://example/pull/9、$0.000） | マージ |"
    assert lines[i + 2] == ""


def test_note_without_section_stops(tmp_path):
    doc = tmp_path / "handoff.md"
    doc.write_text("# 無し\n")
    rep = tmp_path / "r.md"
    rep.write_text("- 結果: 完了\n")
    p = cli("note", str(doc), "--report", str(rep))
    assert p.returncode == 1 and json.loads(p.stdout)["status"] == "stopped"


def test_sync_check_reports_each_check(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    monkeypatch.setattr(sv, "SYNC_CHECKS", [("build", "echo gen > gen.txt"), ("links", "exit 1")])
    res = sv.sync_check(str(tmp_path), commit=False)
    assert res["status"] == "stopped" and res["summary"] == "失敗: links"
    assert [i["result"] for i in res["items"]] == ["ok", "failed"]
    assert validate_result(res, 1) == []


def test_sync_check_commits_generated(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "a@b")
    git(tmp_path, "config", "user.name", "a")
    monkeypatch.setattr(sv, "SYNC_CHECKS", [("build", "echo gen > gen.txt")])
    res = sv.sync_check(str(tmp_path), commit=True)
    assert res["status"] == "ok"
    assert git(tmp_path, "log", "--format=%s").strip() == "Update: 生成物を同期する"
