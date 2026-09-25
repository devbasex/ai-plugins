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
    plan = {"フェーズ": "試験", "課題": [858], "作業場所": str(tmp_path), "steps": steps, **extra}
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


def test_run_step_disables_pytest_reports(tmp_path, monkeypatch):
    # 外側の supervise が足した PYTEST_ADDOPTS を引き継がない
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
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
    assert "pr=9" in s.results["b"]["text"]


@pytest.mark.parametrize("value", ["https://github.com/o/r/pull/9", "https://github.com/o/r/pull/9/", "9", 9])
def test_pr_placeholder_is_number_and_pr_url_is_url(tmp_path, value):
    steps = [{"id": "b", "type": "run", "cmd": "echo n={pr} u={pr_url}", "next": "end"}]
    if isinstance(value, str) and "/pull/" in value:
        s, _ = run_plan(tmp_path, steps, **{"Pull Request": value})
        assert f"n=9 u={value}" in s.results["b"]["text"]
    else:
        s, _ = run_plan(tmp_path, [{**steps[0], "cmd": "echo n={pr}"}], **{"Pull Request": value})
        assert "n=9" in s.results["b"]["text"]


def test_pr_url_from_number_asks_gh(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(f"#!{PY}\nimport sys\nprint('https://github.com/o/r/pull/' + sys.argv[3])\n")
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    s, _ = run_plan(tmp_path, [{"id": "b", "type": "run", "cmd": "echo u={pr_url}", "next": "end"}],
                    **{"Pull Request": "12"})
    assert "u=https://github.com/o/r/pull/12" in s.results["b"]["text"]


def test_drive_args_get_pr_number(tmp_path, monkeypatch):
    drive = tmp_path / "drive.py"
    drive.write_text("import json, sys\nprint(json.dumps({'status': 'ok', 'argv': sys.argv[1:]}))\n")
    monkeypatch.setitem(sv.DRIVES, "fake", drive)
    s, text = run_plan(tmp_path, [{"id": "d", "type": "drive", "drive": "fake", "args": "{pr} --max-rounds 4",
                                   "next": "end"}], **{"Pull Request": "https://github.com/o/r/pull/77"})
    assert "結果: 完了" in text, text
    assert '"argv": ["77", "--max-rounds", "4"]' in s.results["d"]["text"]


@pytest.mark.parametrize("drive", ["cross-review", "cross-refactoring"])
def test_real_drives_take_pr_number_not_url(drive):
    # drive の args の {pr} は番号になる。2 つの駆動は URL を引数の解析で拒む
    p = subprocess.run([PY, str(sv.DRIVES[drive]), "https://github.com/o/r/pull/77"], capture_output=True, text=True)
    assert p.returncode == 2 and "invalid int value" in p.stderr


def test_pr_placeholder_without_pr_fails(tmp_path):
    _, text = run_plan(tmp_path, [{"id": "b", "type": "run", "cmd": "echo {pr}", "next": "end"}])
    assert "結果: 止まった" in text


def test_branch_creates_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "init")
    wt = repo / ".worktrees" / "feat" / "x"
    plan = {"フェーズ": "試験", "課題": [], "作業場所": str(wt), "branch": "feat/x", "起点": "main",
            "steps": [{"id": "t", "type": "run", "cmd": "git rev-parse --abbrev-ref HEAD", "next": "end"}]}
    s = sv.Supervisor(plan, tmp_path / "state")
    assert "結果: 完了" in s.run()
    assert s.results["t"]["text"].strip() == "feat/x"


def test_branch_without_repo_stops(tmp_path):
    plan = {"フェーズ": "試験", "課題": [], "作業場所": str(tmp_path / "nowhere"), "branch": "feat/x",
            "steps": [{"id": "t", "type": "run", "cmd": "true"}]}
    assert "リポジトリ" in sv.Supervisor(plan, tmp_path / "state").run()


def clone_repo(tmp_path):
    """origin を持つ clone を作る。origin/main を起点にすると worktree add が upstream を .git/config へ書く。"""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    git(origin, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "init")
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    return repo


def wt_plan(repo, branch, **extra):
    return {"フェーズ": "試験", "課題": [], "作業場所": str(repo / ".worktrees" / branch), "branch": branch,
            "起点": "origin/main", "steps": [{"id": "t", "type": "run", "cmd": "true", "next": "end"}], **extra}


def test_ensure_worktree_retries_config_lock(tmp_path):
    # 1 回目は branch を作った後の upstream の書き込みで落ちる。やり直しは在る branch を使って作れる
    repo = clone_repo(tmp_path)
    (repo / ".git" / "config.lock").write_text("")
    waits = []
    plan = wt_plan(repo, "feat/a")
    assert sv.ensure_worktree(plan, sleep=waits.append) is None
    assert len(waits) == 1 and 0.5 <= waits[0] <= 2
    assert git(Path(plan["作業場所"]), "rev-parse", "--abbrev-ref", "HEAD").strip() == "feat/a"


def test_ensure_worktree_gives_up_after_retries(tmp_path, monkeypatch):
    repo = clone_repo(tmp_path)
    real = subprocess.run

    def run(cmd, **kw):
        if "worktree" in cmd and "add" in cmd:
            return subprocess.CompletedProcess(cmd, 255, "", "error: could not lock config file .git/config: File exists")
        return real(cmd, **kw)

    monkeypatch.setattr(sv.subprocess, "run", run)
    waits = []
    err = sv.ensure_worktree(wt_plan(repo, "feat/a"), sleep=waits.append)
    assert err.startswith("作業ツリーを作れない") and "could not lock config file" in err
    assert len(waits) == sv.WORKTREE_LOCK_RETRIES


def test_ensure_worktree_other_error_does_not_retry(tmp_path):
    repo = clone_repo(tmp_path)
    waits = []
    err = sv.ensure_worktree(wt_plan(repo, "feat/a", 起点="origin/nothing"), sleep=waits.append)
    assert err.startswith("作業ツリーを作れない") and waits == []


def test_queue_creates_worktrees_in_order_before_run(tmp_path, monkeypatch):
    repo = clone_repo(tmp_path)
    plans, calls = [], []
    for b in ("feat/a", "feat/b", "feat/c"):
        f = tmp_path / f"{b.replace('/', '-')}.json"
        f.write_text(json.dumps(wt_plan(repo, b)))
        plans.append(str(f))
    real = sv.ensure_worktree
    monkeypatch.setattr(sv, "ensure_worktree", lambda plan, **kw: calls.append(plan["branch"]) or real(plan, **kw))
    res = sv.cmd_queue(plans, 3, poll=0.1)
    assert calls == ["feat/a", "feat/b", "feat/c"]
    assert res["status"] == "ok", res
    for b in ("feat/a", "feat/b", "feat/c"):
        assert git(repo / ".worktrees" / b, "rev-parse", "--abbrev-ref", "HEAD").strip() == b


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
        f.write_text(json.dumps({"フェーズ": "試験", "課題": [], "作業場所": str(tmp_path), "steps": [
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
    f.write_text(json.dumps({"フェーズ": "試験", "課題": [], "作業場所": str(tmp_path),
                             "steps": [{"id": "t", "type": "run", "cmd": "exit 1"}]}))
    p = cli("queue", str(f), "--poll", "0.1")
    assert p.returncode == 1
    assert json.loads(p.stdout)["items"][0]["result"] == "止まった"


def queue_plan(tmp_path, name, cmd):
    f = tmp_path / f"{name}.json"
    f.write_text(json.dumps({"フェーズ": "試験", "課題": [], "作業場所": str(tmp_path),
                             "steps": [{"id": "t", "type": "run", "cmd": cmd, "next": "end"}]}))
    return str(f)


def test_queue_then_runs_after_all_done_and_writes_done(tmp_path):
    a = queue_plan(tmp_path, "a", f"date +%s.%N > {tmp_path}/ea")
    b = queue_plan(tmp_path, "b", f"date +%s.%N > {tmp_path}/sb")
    done = tmp_path / "out" / "done.json"
    done.parent.mkdir()
    done.write_text("古い")
    p = cli("queue", a, "--then", b, "--done", str(done), "--poll", "0.1")
    assert p.returncode == 0, p.stdout + p.stderr
    res = json.loads(p.stdout.splitlines()[-1])
    assert [(i["plan"], i["result"]) for i in res["items"]] == [(a, "完了"), (b, "完了")]
    assert float((tmp_path / "sb").read_text()) >= float((tmp_path / "ea").read_text())
    assert json.loads(done.read_text()) == res and res["metrics"]["done"] == str(done)
    assert not list(done.parent.glob(".*.tmp"))


def test_queue_then_not_run_when_one_stops(tmp_path):
    a = queue_plan(tmp_path, "a", "true")
    bad = queue_plan(tmp_path, "bad", "exit 1")
    b = queue_plan(tmp_path, "b", f"touch {tmp_path}/ran")
    p = cli("queue", a, bad, "--then", b, "--poll", "0.1")
    assert p.returncode == 1, p.stdout + p.stderr
    res = json.loads(p.stdout.splitlines()[-1])
    last = res["items"][-1]
    assert last["plan"] == b and last["result"] == "流さなかった" and bad in last["reason"]
    assert not (tmp_path / "ran").exists() and res["metrics"]["not_run"] == 1
    # --done を省くと最初の計画の状態ディレクトリの queue-done.json
    assert json.loads((tmp_path / "a-state" / "queue-done.json").read_text()) == res


def test_queue_removes_old_done_at_start(tmp_path, monkeypatch):
    a = queue_plan(tmp_path, "a", "true")
    done = tmp_path / "a-state" / "queue-done.json"
    done.parent.mkdir()
    done.write_text("古い")
    seen = []
    monkeypatch.setattr(sv, "run_batch", lambda plans, m, poll: seen.append(done.exists()) or [
        {"plan": plans[0], "result": "完了"}])
    res = sv.cmd_queue([a], 3)
    assert seen == [False] and json.loads(done.read_text()) == res


def test_new_release_next_says_then(tmp_path):
    p = cli("new", "release", "--version", "10.17.11-dev.1", "--prs", "995", "--channel", "dev",
            "--worktree", "/r/.worktrees/release/v10.17.11-dev.1", "--out", str(tmp_path / "d.json"))
    assert "--then" in json.loads(p.stdout)["next"]


HANDOFF = """# 引継ぎ

## 今の会話の進み（再開するときはここから読む）

| ミッション | 状態 | 次 |
| --- | --- | --- |
| #1 | 済み | — |

後の段落

## 前の会話の進み

| ミッション | 状態 | 次 |
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


def test_old_plan_and_report_keys_are_read(tmp_path):
    """旧い語（持ち場）で書いた計画と報告も読み、報告は今の語（フェーズ）で書く。"""
    plan = {"持ち場": "試験", "次の持ち場": "検査", "課題": [1], "作業場所": str(tmp_path),
            "steps": [{"id": "t", "type": "run", "cmd": "true", "next": "end"}]}
    text = sv.Supervisor(plan, tmp_path / "state").run()
    assert "## フェーズの報告" in text
    assert "- フェーズ: 試験" in text and "- 次のフェーズ: 検査" in text
    old = "## 持ち場の報告\n\n- 持ち場: 実装\n- 課題: #2\n- 結果: 完了\n"
    assert sv.note_row(old, "") == "| #2 | 実装: 完了 | — |"


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


# --- 利用上限（待ち・認証の切り替え）・関門の終了コード・段の作業場所・配布の雛形 ---

# 呼ばれるたびに responses の次の 1 件を返す偽の claude。呼ばれた順と、足された認証の変数を記録する
SEQ_CLAUDE = """import json, os, sys
state = os.environ["SEQ_DIR"]
n = len(open(os.path.join(state, "calls")).read().splitlines()) if os.path.exists(os.path.join(state, "calls")) else 0
responses = json.load(open(os.path.join(state, "responses.json")))
r = responses[min(n, len(responses) - 1)]
sys.stdin.read()
open(os.path.join(state, "calls"), "a").write(json.dumps({"n": n, "bedrock": os.environ.get("CLAUDE_CODE_USE_BEDROCK")}) + "\\n")
if r.get("stderr"):
    sys.stderr.write(r["stderr"])
print(json.dumps(r["out"]))
sys.exit(r.get("code", 0))
"""
LIMIT = {"out": {"result": "You've hit your session limit · resets 3pm (UTC)", "is_error": True,
                 "api_error_status": 429}, "code": 1}
LIMIT_NO_TIME = {"out": {"result": "API Error: 429", "is_error": True}, "stderr": "HTTP/1.1 429 Too Many Requests\n",
                 "code": 1}
OK = {"out": {"result": "## 作業の報告\n- 結果: 完了", "usage": {}, "total_cost_usd": 0.01, "num_turns": 1}}


@pytest.fixture
def seq(tmp_path, monkeypatch):
    d = tmp_path / "seq"
    d.mkdir()
    fake = d / "claude.py"
    fake.write_text(SEQ_CLAUDE)
    monkeypatch.setenv("SEQ_DIR", str(d))
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE", f"{PY} {fake}")
    monkeypatch.setenv("NDF_SUPERVISE_LIMIT_SLEEP", "0")
    monkeypatch.delenv("NDF_SUPERVISE_CLAUDE_FALLBACK", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)

    def set_responses(*rs):
        (d / "responses.json").write_text(json.dumps(list(rs)))

    def calls():
        f = d / "calls"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []
    return set_responses, calls


WORK_THEN_FAIL = [{"id": "w", "type": "work", "prompt": "直す", "on_fail": "j", "next": "end"},
                  {"id": "j", "type": "judge", "question": "?", "choices": ["stop"]}]


def test_limit_switches_auth_then_waits_then_recovers(tmp_path, seq, monkeypatch):
    set_responses, calls = seq
    # 上限 → 切り替えても上限 → 待って起動し直すと通る
    set_responses(LIMIT, LIMIT, OK)
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE_FALLBACK", "CLAUDE_CODE_USE_BEDROCK=1")
    s, text = run_plan(tmp_path, WORK_THEN_FAIL, limit_wait_max=2 * 86400)  # 解除時刻まで最大 1 日
    assert [c["bedrock"] for c in calls()] == [None, "1", None]
    assert "結果: 完了" in text
    assert "- 認証: 切り替え（CLAUDE_CODE_USE_BEDROCK）" in text
    w = s.results["w"]
    assert w["limit"] is True and w["limit_hits"] == 2 and w["limit_resets"]
    assert "j" not in s.results  # judge を起こさない


def test_limit_fallback_recovers_without_wait(tmp_path, seq, monkeypatch):
    set_responses, calls = seq
    set_responses(LIMIT, OK)
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE_FALLBACK", "CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-east-1")
    s, text = run_plan(tmp_path, WORK_THEN_FAIL)
    assert [c["bedrock"] for c in calls()] == [None, "1"]
    assert "結果: 完了" in text and "認証: 切り替え（CLAUDE_CODE_USE_BEDROCK, AWS_REGION）" in text
    assert "limit_waited" not in s.results["w"]


def test_limit_without_reset_uses_retry_interval(tmp_path, seq):
    set_responses, calls = seq
    set_responses(LIMIT_NO_TIME, LIMIT_NO_TIME, OK)
    s, text = run_plan(tmp_path, WORK_THEN_FAIL, limit_retry_seconds=7)
    assert len(calls()) == 3 and "結果: 完了" in text
    assert s.results["w"]["limit_waited"] == 14
    assert "認証" not in text


def test_limit_wait_over_max_stops_without_judge(tmp_path, seq):
    set_responses, calls = seq
    set_responses(LIMIT_NO_TIME)
    s, text = run_plan(tmp_path, WORK_THEN_FAIL, limit_retry_seconds=10, limit_wait_max=25)
    assert len(calls()) == 3  # 0 秒・10 秒・20 秒の後。次の待ちで 30 秒になり最大を超える
    assert "結果: 止まった" in text and "理由: 利用上限" in text
    assert "j" not in s.results


def test_limit_applies_to_judge(tmp_path, seq):
    set_responses, calls = seq
    set_responses(LIMIT_NO_TIME, {"out": {"result": '{"decision": "next", "reason": "r"}'}})
    s, text = run_plan(tmp_path, [{"id": "j", "type": "judge", "question": "?"},
                                  {"id": "r", "type": "run", "cmd": "true", "next": "end"}],
                       limit_retry_seconds=1)
    assert len(calls()) == 2 and s.results["j"]["decision"] == "next" and "結果: 完了" in text


def test_error_that_is_not_limit_is_plain_failure(tmp_path, seq):
    set_responses, calls = seq
    set_responses({"out": {"result": "boom", "is_error": True}, "code": 1},
                  {"out": {"result": '{"decision": "stop", "reason": "直せない"}'}})
    s, text = run_plan(tmp_path, WORK_THEN_FAIL)
    assert "limit" not in s.results["w"] or s.results["w"]["limit"] is False
    assert s.results["j"]["decision"] == "stop" and "理由: 直せない" in text


@pytest.mark.parametrize("text,expect", [
    ("Claude AI usage limit reached|1760000000", 1760000000.0),
    ("You've hit your limit · resets 3pm (UTC)", "15:00"),
    ("You've hit your session limit · resets at 9:30am (Asia/Tokyo)", "00:30"),
    ("resets 23:05", None),
    ("no time here", "none"),
])
def test_limit_reset_at(text, expect):
    now = 1758790800.0  # 2025-09-25 09:00 UTC
    got = sv.limit_reset_at(text, now)
    if expect == "none":
        assert got is None
    elif isinstance(expect, float):
        assert got == expect
    elif expect is None:
        assert got is not None and 0 < got - now <= 86400
    else:
        from datetime import datetime, timezone
        assert got > now and datetime.fromtimestamp(got, timezone.utc).strftime("%H:%M") == expect


def test_is_usage_limit_matches_monitor_table():
    assert sv.is_usage_limit('{"api_error_status": 429}')
    assert sv.is_usage_limit("You've hit your weekly limit")
    assert not sv.is_usage_limit("ordinary failure")


def test_run_gate_exit_goes_next_and_copies_presentation(tmp_path):
    pres = tmp_path / "gate.md"
    pres.write_text("# 提示物\n")
    out = json.dumps({"tool": "t", "status": "gate", "summary": "s", "items": [], "metrics": {},
                      "presentation_path": str(pres)})
    s, text = run_plan(tmp_path, [
        {"id": "facts", "type": "run", "cmd": f"echo '{out}'; exit 10", "presentation_to": "issues/a.md",
         "on_fail": "bad", "rerun_failed": True, "gate_next": "after"},
        {"id": "bad", "type": "run", "cmd": "false", "next": "end"},
        {"id": "after", "type": "run", "cmd": "true", "next": "end"},
    ])
    copied = tmp_path / "issues" / "a.md"
    assert copied.read_text() == "# 提示物\n"
    assert s.results["facts"]["gate"] is True and "rerun" not in s.results["facts"]
    assert "after" in s.results and "bad" not in s.results
    assert "結果: 関門" in text and f"提示物: {copied}" in text and "関門: 段 facts（exit=10）" in text


def test_run_gate_without_gate_next_uses_next(tmp_path):
    s, text = run_plan(tmp_path, [{"id": "g", "type": "run", "cmd": "exit 12", "on_fail": "bad", "next": "end"},
                                  {"id": "bad", "type": "run", "cmd": "false"}])
    assert list(s.results) == ["g"] and "結果: 関門" in text and "提示物: 無し" in text


def test_work_prompt_uses_step_cwd(tmp_path, fakes):
    other = tmp_path / "other"
    other.mkdir()
    run_plan(tmp_path, [{"id": "w", "type": "work", "prompt": "書く", "cwd": str(other), "next": "end"}])
    assert f"作業場所: {other}\n" in fakes.read_text()


def test_new_release_dev_and_prod(tmp_path):
    wt = "/r/.worktrees/release/v10.17.11-dev.1"
    p = cli("new", "release", "--version", "10.17.11-dev.1", "--prs", "995", "997", "--channel", "dev",
            "--worktree", wt, "--issue", "870", "--out", str(tmp_path / "d.json"))
    assert p.returncode == 0, p.stderr
    plan = json.loads((tmp_path / "d.json").read_text())
    ids = [s["id"] for s in plan["steps"]]
    assert ids == ["bump", "changelog", "notes", "sync", "release", "verify", "facts", "explain", "judge", "fix"]
    st = {s["id"]: s for s in plan["steps"]}
    assert plan["branch"] == "release/v10.17.11-dev.1" and plan["リポジトリ"] == "/r"
    assert "approval-facts --version 10.17.11 --prs 995 997" in st["facts"]["cmd"]
    assert st["facts"]["presentation_to"] == "issues/approval-ndf-v10.17.11.md"
    assert st["facts"]["gate_next"] == "explain" and st["verify"]["cwd"] == "/r"
    assert "--ref develop" in st["verify"]["cmd"] and "--channel dev" in st["release"]["cmd"]

    p = cli("new", "release", "--version", "10.17.11", "--prs", "995", "--channel", "prod",
            "--worktree", "/r/.worktrees/release/v10.17.11", "--out", str(tmp_path / "p.json"))
    plan = json.loads((tmp_path / "p.json").read_text())
    st = {s["id"]: s for s in plan["steps"]}
    assert [s["id"] for s in plan["steps"]] == ["bump", "changelog", "notes", "snapshot", "sync", "release",
                                                "verify", "judge", "fix"]
    assert "--ref main" in st["verify"]["cmd"] and st["verify"]["stage"] == "リリース後テスト"
    assert st["verify"]["next"] == "end" and "facts" not in st


def test_new_release_requires_version():
    p = cli("new", "release", "--worktree", "/r/.worktrees/x", "--channel", "dev")
    assert p.returncode != 0 and "--version" in p.stderr

# --- ミッション（#1005） ------------------------------------------------------------

def test_new_mission_writes_waves_in_order(tmp_path):
    out = tmp_path / "m"
    p = cli("new", "mission", "--name", "v10-18", "--worktree", str(tmp_path), "--issue", "11", "12",
            "--design", "11", "--out", str(out))
    assert p.returncode == 0, p.stderr
    res = json.loads(p.stdout)
    assert res["status"] == "ok"
    manifest = json.loads((out / "mission.json").read_text())
    assert manifest["ブランチ"] == "mission/v10-18"
    assert [w["name"] for w in manifest["波"]] == ["設計", "関門 1", "ミッションのブランチ", "実装", "検査", "配布"]
    waves = {w["name"]: w for w in manifest["波"]}
    assert "plans" not in waves["関門 1"] and waves["関門 1"]["gate"]
    assert len(waves["実装"]["plans"]) == 2 and waves["実装"]["command"].endswith("--max 3")
    for w in manifest["波"]:
        for path in w.get("plans", []):
            plan = json.loads(Path(path).read_text())
            steps = {s["id"]: s for s in plan["steps"]}
            for s in plan["steps"]:
                for k in ("next", "on_fail", "skip_to"):
                    if k in s:
                        assert s[k] in steps or s[k] == "end", (path, s)
                for c in s.get("choices", []):
                    assert c in steps or c in ("stop", "gate"), (path, c)

    design = json.loads(Path(waves["設計"]["plans"][0]).read_text())
    review = next(s for s in design["steps"] if s["id"] == "review")
    assert "--max-rounds" not in review["args"]  # 設計の既定（3 ラウンド）に任せる
    assert design["branch"].startswith("design/")

    impl = json.loads(Path(waves["実装"]["plans"][0]).read_text())
    assert impl["起点"] == "origin/mission/v10-18"
    assert next(s for s in impl["steps"] if s["type"] == "pr")["base"] == "mission/v10-18"

    check = json.loads(Path(waves["検査"]["plans"][0]).read_text())
    assert check["branch"] == "mission/v10-18" and "Pull Request" not in check
    pr = next(s for s in check["steps"] if s["type"] == "pr")
    assert pr["base"] == "develop" and "Closes #11" in pr["summary"] and "Closes #12" in pr["summary"]
    assert [s["id"] for s in check["steps"]][:3] == ["collect", "pr", "assess"]


def test_new_mission_without_design_skips_gate(tmp_path):
    out = tmp_path / "m"
    p = cli("new", "mission", "--name", "m", "--worktree", str(tmp_path), "--issue", "1", "--out", str(out))
    assert p.returncode == 0, p.stderr
    names = [w["name"] for w in json.loads((out / "mission.json").read_text())["波"]]
    assert names == ["ミッションのブランチ", "実装", "検査", "配布"]


@pytest.mark.parametrize("args", [["--issue", "1"], ["--name", "a b", "--issue", "1"], ["--name", "m"]])
def test_new_mission_rejects_bad_args(tmp_path, args):
    assert cli("new", "mission", "--worktree", str(tmp_path), *args).returncode == 2


FAKE_GH_PR = """#!{py}
import json, os, sys
a = sys.argv[1:]
if a[:2] == ["pr", "list"]:
    sys.exit(0)
if a[:2] == ["pr", "create"]:
    open(os.environ["FAKE_GH_BODY"], "w").write(a[a.index("--body") + 1])
    print("https://github.com/o/r/pull/5"); sys.exit(0)
sys.exit(1)
"""


def pr_repo(tmp_path, monkeypatch):
    """develop から切った feat/x を持つリポジトリと、pr create の本文を書き出す偽の gh を用意する。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", str(origin)], check=True)
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "develop")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "commit.gpgsign", "false")
    (root / "a.txt").write_text("a\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    git(root, "remote", "add", "origin", str(origin))
    git(root, "push", "-q", "-u", "origin", "develop")
    git(root, "checkout", "-q", "-b", "feat/x")
    (root / "b.txt").write_text("b\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Add: b")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH_PR.format(py=PY))
    (bindir / "gh").chmod(0o755)
    body = tmp_path / "body.txt"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_BODY", str(body))
    return root, body


def test_pr_body_ends_with_mode_and_passed_stages(tmp_path, monkeypatch):
    root, body = pr_repo(tmp_path, monkeypatch)
    plan = {"フェーズ": "実装", "課題": [1], "モード": "standard", "作業場所": str(root), "steps": [
        {"id": "impl", "type": "run", "cmd": "true", "stage": "実装", "next": "test"},
        {"id": "test", "type": "run", "cmd": "true", "stage": "完了判定", "next": "pr"},
        {"id": "pr", "type": "pr", "stage": "Pull Request", "base": "develop", "body": "template", "next": "end"}]}
    s = sv.Supervisor(plan, tmp_path / "state")
    text = s.run()
    assert "結果: 完了" in text, text
    lines = [l for l in body.read_text().splitlines() if l.strip()]
    assert lines[-2] == "モード: standard / 通した工程: 実装 → 完了判定 → Pull Request"
    assert lines[-1].startswith("🤖 Generated with")
    assert sum(sv.PR_FOOTER in l for l in lines) == 1


@pytest.mark.parametrize("llm_footer", [True, False])
def test_pr_body_from_llm_has_one_footer(tmp_path, monkeypatch, llm_footer):
    root, body = pr_repo(tmp_path, monkeypatch)
    text = "## 概要\n\n本文。" + (f"\n\n{sv.PR_FOOTER}" if llm_footer else "")
    fake = tmp_path / "claude.py"
    fake.write_text("import json, sys\nsys.stdin.read()\n"
                    f"print(json.dumps({{'result': {text!r}, 'usage': {{}}, 'total_cost_usd': 0}}))\n")
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE", f"{PY} {fake}")
    plan = {"フェーズ": "実装", "課題": [1], "作業場所": str(root), "steps": [
        {"id": "pr", "type": "pr", "stage": "Pull Request", "base": "develop", "next": "end"}]}
    s = sv.Supervisor(plan, tmp_path / "state")
    assert "結果: 完了" in s.run()
    got = body.read_text()
    assert "本文。" in got and got.count(sv.PR_FOOTER) == 1
    assert got.rstrip().endswith(sv.PR_FOOTER)


# --- 途中の報告（progress.jsonl）---

FAKE_WORKER = """#!{py}
import json, re, sys, time
prompt = sys.stdin.read()
path = re.search(r"^(\\S+progress\\.jsonl) へ追記する", prompt, re.M).group(1)
lines = {lines!r}
for t in lines:
    with open(path, "a") as f:
        f.write(t + "\\n")
    time.sleep(0.15)
time.sleep({sleep})
print(json.dumps({{"result": "## 作業の報告\\n- 結果: 完了", "usage": {{}}, "total_cost_usd": 0.01,
                  "num_turns": 1}}))
"""


def progress(s):
    rows = []
    for l in (s.dir / "progress.jsonl").read_text().splitlines():
        try:
            rows.append(json.loads(l))
        except json.JSONDecodeError:
            pass  # worker が書いた形の違う行
    return rows


def fake_worker(tmp_path, monkeypatch, lines, sleep=0.0):
    f = tmp_path / "fake_worker.py"
    f.write_text(FAKE_WORKER.format(py=PY, lines=lines, sleep=sleep))
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE", f"{PY} {f}")


def test_step_lines_and_alive_line(tmp_path):
    s, text = run_plan(tmp_path, [{"id": "a", "type": "run", "cmd": "sleep 0.6"},
                                  {"id": "b", "type": "run", "cmd": "echo 最後の行", "next": "end"}],
                       report_interval=0.2)
    assert "結果: 完了" in text
    rows = progress(s)
    steps = [r for r in rows if r["kind"] == "step"]
    assert [r["step"] for r in steps] == ["a", "b"]
    assert steps[0]["next"] == "b" and steps[1]["next"] == "end" and steps[1]["summary"] == "最後の行"
    assert all({"at", "type", "exit", "seconds", "cost"} <= set(r) for r in steps)
    alive = [r for r in rows if r["kind"] == "alive"]
    assert alive and alive[0]["step"] == "a" and alive[0]["worker"] == "無し"
    assert rows.index(alive[0]) < rows.index(steps[0])  # 段の途中で書く
    assert "LLM へ回した 0 回" in text


def test_alive_line_carries_run_step_last_stderr_line(tmp_path):
    """run の段の待ちの間、alive の行に stderr の最後の行を last_output として載せる。"""
    cmd = "echo 'CI のランナー待ち（待ち行列 9 件、待ち 1 件）' >&2; sleep 0.6; echo 結果"
    s, text = run_plan(tmp_path, [{"id": "merge", "type": "run", "cmd": cmd, "next": "end"}],
                       report_interval=0.2)
    assert "結果: 完了" in text
    alive = [r for r in progress(s) if r["kind"] == "alive"]
    assert alive and alive[-1]["last_output"] == "CI のランナー待ち（待ち行列 9 件、待ち 1 件）"


def test_no_alive_line_within_interval(tmp_path):
    s, _ = run_plan(tmp_path, [{"id": "a", "type": "run", "cmd": "sleep 0.3", "next": "end"}])
    assert [r["kind"] for r in progress(s)] == ["step"]


def test_work_prompt_has_progress_instructions(tmp_path, fakes):
    s, _ = run_plan(tmp_path, [{"id": "impl", "type": "work", "prompt": "実装する", "next": "end"}])
    prompt = fakes.read_text()
    assert "## 途中の報告" in prompt and str((s.dir / "progress.jsonl").resolve()) in prompt


def test_worker_lines_are_sorted_into_attention(tmp_path, monkeypatch):
    w = lambda t: json.dumps({"kind": "worker", "at": "2026-01-01T00:00:00+09:00", "text": t}, ensure_ascii=False)
    fake_worker(tmp_path, monkeypatch, [
        w("課題の本文を読み終えた"), "形の違う行", w("テストが 3 件落ちた"), w("テストが 4 件落ちた"),
        w("関門に当たった: 本番の配布"), w("実装を 1 つ終えた")], sleep=0.5)
    s, text = run_plan(tmp_path, [{"id": "impl", "type": "work", "prompt": "実装する", "next": "end"}],
                       report_interval=0.3)
    rows = progress(s)
    att = [r for r in rows if r["kind"] == "attention"]
    assert [a["reason"] for a in att] == ["同じ失敗の繰り返し", "関門"]
    assert all(a["step"] == "impl" for a in att)
    alive = [r for r in rows if r["kind"] == "alive"]
    assert alive and alive[-1]["worker"] == "実装を 1 つ終えた"
    assert "worker 5（形が違う 1）" in text and "conductor 向け 2" in text


def test_repeated_failure_before_judge_is_attention(tmp_path, seq):
    seq[0]({"out": {"result": '{"decision": "t", "reason": "もう 1 度"}', "usage": {}, "total_cost_usd": 0.0}})
    s, _ = run_plan(tmp_path, [
        {"id": "t", "type": "run", "cmd": "exit 1", "on_fail": "j"},
        {"id": "j", "type": "judge", "question": "直すか", "choices": ["t", "stop"]}], 上限=4)
    att = [r for r in progress(s) if r["kind"] == "attention"]
    assert [a["reason"] for a in att] == ["判断の段で stop が出そう"]


def test_queue_notifies_attention(tmp_path):
    f = tmp_path / "p.json"
    prog = tmp_path / "p-state" / "progress.jsonl"
    prog.parent.mkdir()
    prog.write_text(json.dumps({"kind": "attention", "reason": "前の実行"}) + "\n")
    line = json.dumps({"kind": "worker", "at": "x", "text": "進めない: 権限が無い"}, ensure_ascii=False)
    f.write_text(json.dumps({"フェーズ": "試験", "課題": [], "作業場所": str(tmp_path), "report_interval": 0.2,
                             "steps": [{"id": "t", "type": "run",
                                        "cmd": f"echo '{line}' >> {prog}; sleep 0.5", "next": "end"}]}))
    p = cli("queue", str(f), "--poll", "0.1")
    assert p.returncode == 0, p.stdout + p.stderr
    out = [json.loads(l) for l in p.stdout.splitlines()]
    events = [o for o in out if o.get("event") == "attention"]
    assert len(events) == 1 and events[0]["reason"] == "止まった" and events[0]["step"] == "t"
    assert events[0]["tool"] == "supervise-queue" and events[0]["plan"] == str(f)
    assert out[-1]["status"] == "ok"


def test_work_and_judge_prompts_get_separate_work_dirs_per_plan(tmp_path, monkeypatch):
    """並行する 2 つの計画の work の段は、状態ディレクトリの下の別々の作業ディレクトリを渡される。"""
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE_CLAUDE.format(py=PY))
    log = tmp_path / "claude.log"
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE", f"{PY} {fake}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    dirs = []
    for name in ("plan-a", "plan-b"):
        plan = {"フェーズ": "試験", "課題": [985], "作業場所": str(tmp_path),
                "steps": [{"id": "w", "type": "work", "prompt": "書く", "next": "end"}]}
        s = sv.Supervisor(plan, tmp_path / name)
        assert "結果: 完了" in s.run()
        work = (tmp_path / name / "work").resolve()
        assert work.is_dir()
        dirs.append(work)
    prompts = log.read_text().split("\n=====\n")
    assert f"作業ディレクトリ: {dirs[0]}" in prompts[0] and str(dirs[1]) not in prompts[0]
    assert f"作業ディレクトリ: {dirs[1]}" in prompts[1] and str(dirs[0]) not in prompts[1]
    assert dirs[0] != dirs[1]
