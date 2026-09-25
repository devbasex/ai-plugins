"""cross-review / cross-refactoring の drive.py と、supervise.py の drive の段・worker のランタイム。

駆動が呼ぶスクリプトは `call` を差し替えて模す。gh と claude は PATH の先頭に置いた偽物。実機の claude は起動しない。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILLS = SCRIPTS.parent / "skills"
PY = sys.executable
sys.path.insert(0, str(SCRIPTS / "lib"))
from step_result import validate_result  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cr = load("cr_drive", SKILLS / "cross-review" / "scripts" / "drive.py")
rf = load("rf_drive", SKILLS / "cross-refactoring" / "scripts" / "drive.py")
sv = load("supervise_drive", SCRIPTS / "supervise.py")


def run_main(mod, argv, capsys):
    with pytest.raises(SystemExit) as e:
        mod.main(argv)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert validate_result(out, e.value.code) == []
    return e.value.code, out


# --- cross-review ---------------------------------------------------------------

class FakeReview:
    """state.py などの応答を模す。judges は判定の終了コードを順に返す。"""

    def __init__(self, tmp: Path, judges, rotate=False):
        self.tmp, self.judges, self.rotate = tmp, list(judges), rotate
        self.calls = []
        self.state = {"repo": "o/r", "current_pr": 5, "worktree_path": str(tmp / "wt"), "head_branch": "feat/x",
                      "base_branch": "develop", "rounds": [], "pr_history": [{"pr": 5}]}
        self.save()

    def save(self):
        (self.tmp / "cross-review-pr5-state.json").write_text(json.dumps(self.state))

    def __call__(self, cmd, env=None, cwd=None):
        name = Path(cmd[1]).name if cmd[0] in (PY, "bash") else cmd[0]
        args = cmd[2:]
        self.calls.append((name, *args))
        if name == "state.py":
            sub = args[0]
            if sub == "init":
                return 0, f"PR=5\nTMP_DIR={self.tmp}\nWORKTREE={self.tmp / 'wt'}\n"
            if sub == "start-round":
                if not self.judges:
                    return 1, ""
                n = len(self.state["rounds"]) + 1
                self.state["rounds"].append({"round": n, "reviewers": ["codex", "kiro"],
                                             "codex": {"intent": "REQUEST_CHANGES", "comments": 3,
                                                       "review_url": "https://x/r1"},
                                             "kiro": {"intent": "APPROVE", "comments": 1}})
                self.save()
                return 0, f"ROUND={n}\nREVIEWERS='codex kiro'\nREVIEWERS_CSV=codex,kiro\n"
            if sub == "judge":
                rc = self.judges.pop(0)
                if rc == 0:
                    self.state["final"] = "approved"
                    self.save()
                return rc, ""
            if sub == "check-oscillation":
                return 2, ""
            if sub == "merge-fix":
                self.state["rounds"][-1]["fix"] = {"fixed": 2, "rejected": 1}
                self.save()
                return 0, ""
            if sub == "should-rotate":
                return (0 if self.rotate else 2), ""
            if sub == "verify-sweep":
                self.state["sweep"] = {"remaining_open": 1, "verified": True}
                self.save()
                return 6, ""
            if sub == "report":
                return 0, "## 報告\n"
            return 0, ""
        if name == "rotate-pr.sh" and args[0] == "execute":
            return 0, "NEW_PR=9\nNEW_PR_URL=https://x/9\nNEW_BRANCH=feat/x\n"
        return 0, ""


def test_review_drive_pauses_for_fix_then_sweep_then_finishes(tmp_path, monkeypatch, capsys):
    fake = FakeReview(tmp_path, judges=[2, 0])
    monkeypatch.setattr(cr, "call", fake)
    code, out = run_main(cr, ["5", "--max-rounds", "4"], capsys)
    assert code == 20 and out["status"] == "gate" and out["next"] == "fix"
    item = out["items"][0]
    assert item["pause"] == "fix" and item["round"] == 1
    prompt = Path(item["prompt_file"]).read_text()
    assert "/ndf:fix 5 --defer-nit" in prompt and str(tmp_path / "wt") in prompt and "https://x/r1" in prompt
    assert "pint" not in prompt
    assert ("state.py", "init", "5", "--max-rounds", "4") in fake.calls

    # 打ち直しても結果ファイルが無ければ同じ pause を返す
    code, out = run_main(cr, ["5"], capsys)
    assert code == 20

    Path(item["result_file"]).write_text("{}")
    code, out = run_main(cr, ["5"], capsys)
    assert code == 21 and out["items"][0]["pause"] == "sweep"
    assert any(c[:2] == ("state.py", "merge-fix") for c in fake.calls)

    Path(out["items"][0]["result_file"]).write_text("{}")
    code, out = run_main(cr, ["5"], capsys)
    assert code == 0 and out["status"] == "ok"
    m = out["metrics"]
    assert (m["rounds"], m["findings"], m["fixed"], m["rejected"], m["unresolved"], m["final"]) == \
        (2, 8, 2, 1, 1, "approved")
    assert any(c[0] == "result_posts.py" for c in fake.calls)
    assert Path(out["items"][0]["report"]).read_text() == "## 報告\n"


def test_review_drive_light_rotation_pauses_for_newtext(tmp_path, monkeypatch, capsys):
    fake = FakeReview(tmp_path, judges=[2, 0], rotate=True)
    monkeypatch.setattr(cr, "call", fake)
    _, out = run_main(cr, ["5"], capsys)
    Path(out["items"][0]["result_file"]).write_text("{}")
    code, out = run_main(cr, ["5"], capsys)
    assert code == 22 and out["items"][0]["pause"] == "newtext"
    assert "Step 6b" in Path(out["items"][0]["prompt_file"]).read_text()
    Path(out["items"][0]["result_file"]).write_text('{"title": "t", "body": "b"}')
    code, out = run_main(cr, ["5"], capsys)
    assert code == 21
    assert ("state.py", "set-current-pr", "5", "9", "--head-branch", "feat/x") in fake.calls


def test_review_drive_stops_when_init_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cr, "call", lambda cmd, env=None, cwd=None: (3, ""))
    code, out = run_main(cr, ["5"], capsys)
    assert code == 1 and out["status"] == "stopped" and out["metrics"]["exit"] == 3


def test_both_drives_share_the_pause_table():
    assert cr.PAUSES == rf.PAUSES


# --- cross-refactoring ----------------------------------------------------------

class FakeRefactor:
    def __init__(self, tmp: Path, gate: str, rc: dict | None = None):
        self.tmp, self.gate, self.rc = tmp, gate, rc or {}
        self.calls = []
        items = [{"status": "verified", "fix_count": 1}, {"status": "reverted"}, {"status": "deferred"}]
        (tmp / "cross-refactoring-rf7-state.json").write_text(json.dumps({"items": items}))

    def __call__(self, cmd, env=None, cwd=None):
        name = Path(cmd[1]).name if cmd[0] in (PY, "bash") else cmd[0]
        args = cmd[2:]
        self.calls.append((name, *args))
        if name == "refactor.py":
            sub = args[0]
            if sub in self.rc:
                return self.rc[sub], ""
            if sub == "init":
                return 0, (f"ID=7\nTMP_DIR={self.tmp}\nPHASE=propose\nIMPL=codex\nRUNTIMES='codex kiro'\n"
                           f"RUNTIMES_CSV=codex,kiro\nWORK={self.tmp}\n")
            if sub == "merge-proposals":
                return 2, ""  # 候補 0 件 → 最終ゲートへ
            if sub == "final-gate":
                return 0, f"FINAL_GATE={self.gate}\n"
            if sub == "report":
                return 0, "## 構造改善の報告\n"
            return 0, ""
        return 0, "abc\n"


def test_refactor_drive_workflow_step_finishes_with_counts(tmp_path, monkeypatch, capsys):
    fake = FakeRefactor(tmp_path, "passed")
    monkeypatch.setattr(rf, "call", fake)
    code, out = run_main(rf, ["5", "--workflow-step", "--scope", "src", "--baseline-test", "pytest"], capsys)
    assert code == 0 and out["status"] == "ok"
    m = out["metrics"]
    assert (m["items"], m["adopted"], m["reverted"], m["deferred"], m["fix_rounds"]) == (3, 1, 1, 1, 1)
    assert ("refactor.py", "finalize", "7") in fake.calls
    assert not any(c[:2] == ("refactor.py", "merge-plan") for c in fake.calls)


def test_refactor_drive_pauses_for_cross_review_then_finalizes(tmp_path, monkeypatch, capsys):
    fake = FakeRefactor(tmp_path, "cross-review")
    monkeypatch.setattr(rf, "call", fake)
    code, out = run_main(rf, ["5", "--scope", "src", "--baseline-test", "pytest"], capsys)
    assert code == 23 and out["items"][0]["pause"] == "cross-review"
    assert "cross-review/scripts/drive.py 5" in out["items"][0]["command"]
    Path(out["items"][0]["result_file"]).write_text('{"review_status": "approved"}')
    code, out = run_main(rf, ["5", "--scope", "src", "--baseline-test", "pytest"], capsys)
    assert code == 0 and out["metrics"]["review_status"] == "approved"
    assert ("refactor.py", "finalize", "7", "--review-status", "approved") in fake.calls


def test_refactor_drive_stops_on_abort(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rf, "call", FakeRefactor(tmp_path, "passed", rc={"merge-proposals": 4}))
    code, out = run_main(rf, ["5", "--scope", "src", "--baseline-test", "pytest"], capsys)
    assert code == 1 and out["metrics"]["exit"] == 4


def test_review_status_requires_clean_sweep():
    ok = {"final": "approved", "sweep": {"verified": True, "remaining_open": 0, "commit": None}}
    assert rf.review_status(ok) == cr.review_status(ok) == "approved"
    swept = {**ok, "sweep": {**ok["sweep"], "commit": "abc"}}  # スイープが直した HEAD は承認されていない
    assert rf.review_status(swept) == cr.review_status(swept) == "unverified"
    assert cr.review_status({"final": "max_rounds"}) == "max_rounds"


# --- supervise.py の drive の段 ------------------------------------------------

FAKE_DRIVE = """#!{py}
import json, sys
from pathlib import Path
d = Path({d!r})
res = d / "fix-result.json"
if res.is_file():
    print(json.dumps({{"tool": "t", "status": "ok", "summary": "done", "items": [],
                      "metrics": {{"rounds": 2, "findings": 3, "unresolved": 0, "review_status": "approved"}}}}))
    sys.exit(0)
(d / "prompt.md").write_text("PR を直す指示")
print(json.dumps({{"tool": "t", "status": "gate", "summary": "fix", "next": "fix", "metrics": {{}},
                  "items": [{{"pause": "fix", "prompt_file": str(d / "prompt.md"), "result_file": str(res),
                              "round": 1}}]}}))
sys.exit(20)
"""

FAKE_CLAUDE = """#!{py}
import json, os, re, sys
text = sys.stdin.read()
open(os.environ["FAKE_CLAUDE_LOG"], "a").write(text + "\\n=====\\n")
m = re.search(r"結果ファイル (\\S+) を書く", text)
if m and os.environ.get("FAKE_CLAUDE_WRITE", "1") == "1":
    open(m.group(1), "w").write("{{}}")
print(json.dumps({{"result": "## 作業の報告\\n- 結果: 完了", "usage": {{"output_tokens": 5}},
                  "total_cost_usd": 0.01, "num_turns": 2}}))
"""


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    f = bindir / "claude"
    f.write_text(FAKE_CLAUDE.format(py=PY))
    f.chmod(0o755)
    log = tmp_path / "claude.log"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.delenv("NDF_SUPERVISE_CLAUDE", raising=False)
    return log


def fake_drive(tmp_path) -> str:
    d = tmp_path / "drive"
    d.mkdir(exist_ok=True)
    f = d / "drive.py"
    f.write_text(FAKE_DRIVE.format(py=PY, d=str(d)))
    return f"{PY} {f}"


def run_plan(tmp_path, steps):
    plan = {"持ち場": "検査", "課題": [870], "作業場所": str(tmp_path), "steps": steps}
    s = sv.Supervisor(plan, tmp_path / "state")
    return s, s.run()


def test_drive_step_hands_pause_to_worker_and_reports_counts(tmp_path, fake_claude):
    s, text = run_plan(tmp_path, [{"id": "review", "type": "drive", "cmd": fake_drive(tmp_path), "next": "end"}])
    assert "結果: 完了" in text
    assert s.results["review"]["pauses"] == ["fix"]
    assert "- 件数: review: rounds 2 / findings 3 / unresolved 0" in text
    prompt = fake_claude.read_text()
    assert "作業: fix" in prompt and "PR を直す指示" in prompt
    assert s.llm["work"] == 1


def test_drive_step_fails_when_worker_writes_no_result(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", "0")
    s, text = run_plan(tmp_path, [{"id": "review", "type": "drive", "cmd": fake_drive(tmp_path), "next": "end"}])
    assert "結果: 止まった" in text
    assert "結果ファイルを書かなかった" in s.results["review"]["text"]


def test_drive_step_runs_nested_command_pause(tmp_path, fake_claude):
    inner = fake_drive(tmp_path)
    (tmp_path / "drive" / "fix-result.json").write_text("{}")  # 内側はすぐ終わる
    outer = tmp_path / "outer.py"
    res = tmp_path / "cr.json"
    outer.write_text(f"""import json, sys
from pathlib import Path
res = Path({str(res)!r})
if res.is_file():
    print(json.dumps({{"tool": "o", "status": "ok", "summary": "s", "items": [],
                      "metrics": {{"review_status": json.loads(res.read_text())["review_status"]}}}}))
    sys.exit(0)
print(json.dumps({{"tool": "o", "status": "gate", "summary": "s", "next": "cross-review", "metrics": {{}},
                  "items": [{{"pause": "cross-review", "result_file": str(res), "command": {inner!r}}}]}}))
sys.exit(23)
""")
    s, text = run_plan(tmp_path, [{"id": "refactor", "type": "drive", "cmd": f"{PY} {outer}", "next": "end"}])
    assert "結果: 完了" in text
    assert s.results["refactor"]["counts"] == {"review_status": "approved"}


def test_drive_step_resolves_known_drive(tmp_path):
    s = sv.Supervisor({"作業場所": str(tmp_path), "steps": [{"id": "x", "type": "run", "cmd": "true"}]},
                      tmp_path / "st")
    cmd = s.drive_cmd({"drive": "cross-review", "args": "5 --max-rounds 4"})
    assert cmd.endswith("cross-review/scripts/drive.py 5 --max-rounds 4")


def test_work_step_with_runtime_uses_external_ai(tmp_path, monkeypatch):
    fake = tmp_path / "external-ai.py"
    fake.write_text("""import json, sys
a = sys.argv[1:]
out = a[a.index("--output-file") + 1]
prompt = open(a[a.index("--prompt-file") + 1]).read()
open(out, "w").write("## 作業の報告\\n- 結果: 完了\\n" + a[1] + "\\n" + prompt[-20:])
print(json.dumps({"tool": "external-ai", "status": "ok", "summary": "ok", "items": [], "metrics": {}}))
""")
    monkeypatch.setattr(sv, "EXTERNAL_AI", fake)
    s, text = run_plan(tmp_path, [{"id": "impl", "type": "work", "runtime": "codex", "prompt": "実装する",
                                   "next": "end"}])
    assert "結果: 完了" in text
    assert "codex" in s.results["impl"]["text"]
    assert "作業の報告" in (tmp_path / "state" / "impl-prompt.md").read_text()


def test_new_check_with_scope_uses_drive_steps(tmp_path):
    import argparse
    a = argparse.Namespace(pr=998, scope=["plugins/ndf/scripts"], issue=[870], mode="standard",
                           worktree=str(tmp_path))
    steps = {s["id"]: s for s in sv.plan_check(a)["steps"]}
    assert steps["refactor"]["type"] == "drive" and "--workflow-step" in steps["refactor"]["args"]
    assert steps["review"]["type"] == "drive" and steps["review"]["drive"] == "cross-review"
    a.scope = []
    assert sv.plan_check(a)["steps"][1]["type"] == "work"
