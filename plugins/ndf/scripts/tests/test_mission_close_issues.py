"""mission-close.py の --issues と --record-pr 0（#1078）。

`pace: fast` の実装 Pull Request は閉じる語を持たないため、閉じる課題を --issues で受ける。最終の検査で
変更が無く本番を飛ばしたときは、配布の記録を読まずに閉じる（--record-pr 0）。gh の偽物は
test_mission_close_merge_green.py のものを使う。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("mission_close_harness", HERE / "test_mission_close_merge_green.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)
call, issues_of, DIST_PROD, PY = harness.call, harness.issues_of, harness.DIST_PROD, harness.PY


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for args in (["init", "-q", "-b", "develop"], ["config", "user.email", "t@example.com"],
                 ["config", "user.name", "t"], ["config", "commit.gpgsign", "false"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / "keep.txt").write_text("x\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True)
    return root


@pytest.fixture
def gh(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    f = bindir / "gh"
    f.write_text(harness.FAKE_GH.format(py=PY), encoding="utf-8")
    f.chmod(0o755)
    state = tmp_path / "gh-state.json"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    env.update(PATH=f"{bindir}{os.pathsep}{env['PATH']}", FAKE_GH_STATE=str(state))

    class G:
        def set(self, **kw):
            state.write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")

        def get(self):
            return json.loads(state.read_text(encoding="utf-8"))
    g = G()
    g.env = env
    g.set()
    return g


def test_record_zero_closes_the_given_issues_without_reading_a_record(repo, gh):
    gh.set(issues={"o/r#1": ["OPEN"], "o/r#2": ["CLOSED"]})
    code, out, err = call("mission-close.py", ["--record-pr", "0", "--issues", "1,2", "--repo", "o/r",
                                               "--with-verification"], gh.env, repo)
    assert code == 0, (out, err)
    res = issues_of(out)
    assert res["o/r#1"]["result"] == "closed" and res["o/r#2"]["result"] == "already_closed"
    assert not [c for c in gh.get()["calls"] if "body,comments" in c]


def test_record_zero_alone_returns_two(repo, gh):
    code, out, err = call("mission-close.py", ["--record-pr", "0", "--repo", "o/r"], gh.env, repo)
    assert code == 2 and out["status"] == "stopped"
    assert gh.get().get("calls", []) == []


def test_issues_are_added_to_the_closing_words_of_the_prs(repo, gh):
    gh.set(records={"20": {"body": DIST_PROD, "comments": []}}, bodies={"11": "Fixes #1", "12": ""},
           issues={"o/r#1": ["OPEN"], "o/r#3": ["OPEN"]})
    code, out, err = call("mission-close.py", ["--record-pr", "20", "--repo", "o/r", "--issues", "3,1"],
                          gh.env, repo)
    assert code == 0, (out, err)
    assert sorted(issues_of(out)) == ["o/r#1", "o/r#3"]
    assert all(i["result"] == "closed" for i in issues_of(out).values())


def test_issues_alone_do_not_need_the_mission_prs(repo, gh):
    gh.set(records={"20": {"body": "## 配布の記録\n段階: 本番（2026-09-25 承認）\n版: 1.0.0 → 1.1.0\n", "comments": []}},
           issues={"o/r#5": ["OPEN"]})
    code, out, err = call("mission-close.py", ["--record-pr", "20", "--repo", "o/r", "--issues", "5"], gh.env, repo)
    assert code == 0, (out, err)
    assert issues_of(out)["o/r#5"]["result"] == "closed"
