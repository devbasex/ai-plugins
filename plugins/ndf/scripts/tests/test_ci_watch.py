"""ci-watch.py peek: PR の CI のテストのチェックを待たずに 1 回だけ読み、改修の回数を上限で数える（#3056 の I4〜I6）。

gh は PATH の先頭に置いた偽物で置き換える。偽物は FAKE_GH_STATE の JSON を読む。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "ci-watch.py"
PY = sys.executable
sys.path.insert(0, str(SCRIPTS / "lib"))

import ci_watch  # noqa: E402

FAKE_GH = r"""#!{py}
import json, os, sys
a = sys.argv[1:]
st = json.load(open(os.environ["FAKE_GH_STATE"], encoding="utf-8"))
if a[:2] == ["pr", "view"]:
    pr = st.get("pr")
    if pr is None:
        sys.stderr.write("HTTP 502\n"); sys.exit(1)
    print(json.dumps(pr)); sys.exit(0)
if a[:2] == ["run", "view"] and "--log-failed" in a:
    print("\n".join(f"line {{i}}" for i in range(299)))
    print("line 299 https://x-access-token:ghs_SECRETSECRET1234@github.com/o/r Authorization: Bearer abc.def"); sys.exit(0)
sys.exit(1)
"""


def check(name, status="COMPLETED", conclusion="SUCCESS", workflow="CI"):
    return {
        "__typename": "CheckRun",
        "name": name,
        "workflowName": workflow,
        "status": status,
        "conclusion": conclusion,
        "detailsUrl": "https://github.com/o/r/actions/runs/11/job/22",
    }


@pytest.fixture
def repo(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH.format(py=PY))
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_STATE", str(tmp_path / "gh.json"))
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".ndf").mkdir()
    test = {"ci": {"only": True, "check": "pytest"}, "suites": [{"name": "t", "runner": "pytest", "command": "pytest"}]}
    (root / ".ndf" / "project.json").write_text(json.dumps({"test": test}), encoding="utf-8")
    return root


def peek(tmp_path, root, head, *checks, after_merge=False, pr=True):
    state = {"pr": {"headRefOid": head, "statusCheckRollup": list(checks)}} if pr else {}
    (tmp_path / "gh.json").write_text(json.dumps(state))
    args = [PY, str(SCRIPT), "peek", "--pr", "7", "--state", str(tmp_path / "st"), "--root", str(root)]
    p = subprocess.run(args + (["--after-merge"] if after_merge else []), capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1]), p.stdout


def test_pending_goes_on_and_is_recorded(tmp_path, repo):
    code, out, _ = peek(tmp_path, repo, "a1", check("pytest", status="IN_PROGRESS", conclusion=""))
    assert code == 0 and out["metrics"]["verdict"] == "pending"
    rows = ci_watch.read_watch_log(tmp_path / "st")
    assert rows[-1]["head"] == "a1" and rows[-1]["verdict"] == "pending"


def test_only_the_test_check_decides(tmp_path, repo):
    code, out, _ = peek(tmp_path, repo, "a1", check("pytest"), check("lint", conclusion="FAILURE"))
    assert code == 0 and out["metrics"]["verdict"] == "passed"
    code, out, _ = peek(tmp_path, repo, "a1", check("pytest", conclusion="SKIPPED"))
    assert code == 0 and out["metrics"]["verdict"] == "passed"


def test_absent_test_check_is_pending_not_passed(tmp_path, repo):
    code, out, _ = peek(tmp_path, repo, "a1", check("lint"))
    assert code == 0 and out["metrics"]["verdict"] == "pending"
    assert {"kind": "check", "name": "pytest", "result": "absent"} in out["items"]


def test_failure_asks_for_a_fix_with_the_log_tail(tmp_path, repo):
    code, out, text = peek(tmp_path, repo, "a1", check("pytest", conclusion="FAILURE"))
    assert code == 1 and out["metrics"]["fixes"] == 1
    assert "line 299" in text and "line 99\n" not in text


def test_failed_heads_are_counted_and_the_limit_stops(tmp_path, repo):
    fail = check("pytest", conclusion="FAILURE")
    for head in ("h1", "h2", "h3"):
        code, out, _ = peek(tmp_path, repo, head, fail)
        assert code == 1
    assert out["metrics"]["fixes"] == 3
    code, out, _ = peek(tmp_path, repo, "h4", fail)
    assert code == 2 and out["status"] == "stopped"


def test_a_fix_that_left_the_head_unchanged_stops_and_counts_once(tmp_path, repo):
    fail = check("pytest", conclusion="FAILURE")
    assert peek(tmp_path, repo, "h1", fail)[0] == 1
    code, out, _ = peek(tmp_path, repo, "h1", fail)
    assert code == 2 and out["metrics"]["fixes"] == 1 and "head を変えずに" in out["summary"]


def test_the_failed_log_is_read_once_per_run(tmp_path, repo):
    fails = [check(n, conclusion="FAILURE") for n in ("pytest (3.11)", "pytest (3.12)")]
    (repo / ".ndf" / "project.json").write_text(json.dumps({"test": {"ci": {"only": True}, "suites": []}}), encoding="utf-8")
    code, _, text = peek(tmp_path, repo, "a1", *fails)
    assert code == 1 and text.count("line 299") == 1 and "CI / pytest (3.11), CI / pytest (3.12) の失敗した" in text
    assert "ghs_SECRET" not in text and "abc.def" not in text and "x-access-token:***" not in text and "***@github.com" in text


def test_unreadable_is_recorded_and_goes_on(tmp_path, repo):
    code, out, _ = peek(tmp_path, repo, None, pr=False)
    assert code == 0 and out["metrics"]["verdict"] == "unknown"
    assert ci_watch.read_watch_log(tmp_path / "st")[-1]["verdict"] == "unknown"


def test_after_merge_without_a_test_failure_stops(tmp_path, repo):
    code, _, _ = peek(tmp_path, repo, "a1", check("pytest"), after_merge=True)
    assert code == 3
    code, _, _ = peek(tmp_path, repo, "a1", check("pytest", conclusion="FAILURE"), after_merge=True)
    assert code == 1


def test_decide_counts_failed_heads_before_this_one():
    rows = [{"head": h, "verdict": "failed"} for h in ("a", "b", "c")]
    assert ci_watch.decide_exit("failed", "c", rows[:2], 3, False) == (1, 3)
    assert ci_watch.decide_exit("failed", "c", rows, 3, False) == (2, 3)
    assert ci_watch.decide_exit("failed", "d", rows, 3, False) == (2, 4)
    assert ci_watch.decide_exit("pending", "d", rows, 3, False) == (0, 3)


def test_a_lagging_pr_head_is_pending_not_a_failure(tmp_path, repo):
    """push の直後に PR の先端（headRefOid）が作業ディレクトリの HEAD に追いつかないうちは、前の先端の失敗を数えない。"""
    fail = check("pytest", conclusion="FAILURE")
    assert peek(tmp_path, repo, "h1", fail)[0] == 1
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "x"], check=True
    )
    code, out, text = peek(tmp_path, repo, "h1", fail)
    assert code == 0 and out["metrics"]["verdict"] == "pending" and "揃っていない" in out["summary"]
    assert "落ちたチェック" not in text and ci_watch.failed_heads(ci_watch.read_watch_log(tmp_path / "st")) == ["h1"]
