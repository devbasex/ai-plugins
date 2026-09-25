"""merged-steps.py probe: 開いた PR のチェックの分類と、取り残しの再実行。

gh は PATH の先頭に置いた偽物で置き換える。偽物は FAKE_GH_STATE の JSON を読み、呼ばれた引数を calls に積む。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
MERGED = SCRIPTS / "merged-steps.py"
PY = sys.executable

FAKE_GH = r'''#!{py}
import json, os, sys
a = sys.argv[1:]
path = os.environ["FAKE_GH_STATE"]
st = json.load(open(path, encoding="utf-8"))
st.setdefault("calls", []).append(a)
json.dump(st, open(path, "w", encoding="utf-8"))
out, code = None, 0
if a[:2] == ["pr", "view"]:
    pr = st.get("prs", {{}}).get(a[2])
    out, code = (json.dumps(pr), 0) if pr else (None, 1)
elif a[:2] == ["pr", "list"]:
    head = a[a.index("--head") + 1]
    out = json.dumps([{{"number": n}} for n in st.get("heads", {{}}).get(head, [])])
elif a[:2] == ["run", "view"]:
    r = st.get("runs", {{}}).get(a[2])
    out, code = (json.dumps(r), 0) if r else (None, 1)
elif a[:2] == ["run", "rerun"]:
    code = st.get("rerun_code", 0)
    if code:
        sys.stderr.write("rerun failed\n")
elif a[:2] == ["run", "list"]:
    out = json.dumps([{{"databaseId": i}} for i in range(st.get("queued_runs", 0))])
else:
    code = 1
if out is not None:
    print(out)
sys.exit(code)
'''


def check(name, status, conclusion="", run_id="11", job_id="22"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion,
            "detailsUrl": f"https://github.com/o/r/actions/runs/{run_id}/job/{job_id}"}


def pr(n, *checks, state="OPEN"):
    return {"number": n, "state": state, "statusCheckRollup": list(checks)}


@pytest.fixture
def gh(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    f = bindir / "gh"
    f.write_text(FAKE_GH.format(py=PY))
    f.chmod(0o755)
    state = tmp_path / "gh.json"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_STATE", str(state))
    subprocess.run(["git", "init", "-q", str(tmp_path / "repo")], check=True)

    def setup(**st):
        state.write_text(json.dumps(st))
        return state
    return setup


def probe(tmp_path, *args):
    p = subprocess.run([PY, str(MERGED), "probe", *args, "--root", str(tmp_path / "repo")],
                       capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else None


def calls(state):
    return json.loads(state.read_text())["calls"]


def stale_setup(gh, attempt):
    return gh(prs={"1100": pr(1100, check("runtime-plugin-build-check", "IN_PROGRESS"))},
              runs={"11": {"status": "completed", "attempt": attempt,
                           "jobs": [{"databaseId": 22, "status": "in_progress", "conclusion": ""}]}})


def test_stale_with_act_reruns_job_and_is_remedied(tmp_path, gh):
    state = stale_setup(gh, 1)
    code, out = probe(tmp_path, "--pr", "1100", "--act")
    assert code == 0
    assert out["metrics"]["class"] == "stale" and out["metrics"]["action"] == "remedied"
    assert ["run", "rerun", "11", "--job", "22"] in calls(state)
    assert out["metrics"]["prs"] == [1100]


def test_stale_without_act_is_judge_and_does_not_rerun(tmp_path, gh):
    state = stale_setup(gh, 1)
    _, out = probe(tmp_path, "--pr", "1100")
    assert (out["metrics"]["class"], out["metrics"]["action"]) == ("stale", "judge")
    assert not any(c[:2] == ["run", "rerun"] for c in calls(state))


def test_stale_again_after_rerun_is_judge(tmp_path, gh):
    state = stale_setup(gh, 2)
    _, out = probe(tmp_path, "--pr", "1100", "--act")
    assert (out["metrics"]["class"], out["metrics"]["action"]) == ("stale_again", "judge")
    assert not any(c[:2] == ["run", "rerun"] for c in calls(state))


def test_rerun_failure_is_judge(tmp_path, gh):
    st = json.loads(stale_setup(gh, 1).read_text())
    gh(**st, rerun_code=1)
    _, out = probe(tmp_path, "--pr", "1100", "--act")
    assert (out["metrics"]["class"], out["metrics"]["action"]) == ("stale", "judge")


@pytest.mark.parametrize("checks,runs,expected", [
    ([check("a", "COMPLETED", "FAILURE")], {}, ("failed", "fix")),
    ([check("a", "IN_PROGRESS")], {"11": {"status": "completed", "attempt": 1,
                                         "jobs": [{"databaseId": 22, "conclusion": "success"}]}}, ("settled", "wait")),
    ([check("a", "QUEUED")], {"11": {"status": "queued", "attempt": 1,
                                    "jobs": [{"databaseId": 22, "status": "queued"}]}}, ("queued", "wait")),
    ([check("a", "IN_PROGRESS")], {"11": {"status": "in_progress", "attempt": 1,
                                         "jobs": [{"databaseId": 22, "status": "in_progress"}]}}, ("running", "wait")),
    ([check("a", "COMPLETED", "SUCCESS")], {}, ("passed", "judge")),
])
def test_classes(tmp_path, gh, checks, runs, expected):
    gh(prs={"5": pr(5, *checks)}, runs=runs, queued_runs=4)
    _, out = probe(tmp_path, "--pr", "5")
    assert (out["metrics"]["class"], out["metrics"]["action"]) == expected
    if expected[0] == "queued":
        assert out["metrics"]["queued_runs"] == 4


def test_strongest_class_wins_across_prs_found_by_head(tmp_path, gh):
    gh(heads={"release/v1": [7], "develop": [8]},
       prs={"7": pr(7, check("a", "COMPLETED", "SUCCESS")), "8": pr(8, check("b", "COMPLETED", "FAILURE"))})
    _, out = probe(tmp_path, "--head", "release/v1", "--head", "develop")
    assert out["metrics"]["class"] == "failed" and out["metrics"]["prs"] == [7, 8]


def test_no_open_pr_is_none(tmp_path, gh):
    gh(prs={"3": pr(3, state="MERGED")})
    code, out = probe(tmp_path, "--pr", "3", "--pr", "4")
    assert code == 0 and (out["metrics"]["class"], out["metrics"]["action"]) == ("none", "judge")


def test_needs_pr_or_head(tmp_path, gh):
    gh()
    code, _ = probe(tmp_path)
    assert code == 2
