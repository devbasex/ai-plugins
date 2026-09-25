"""fix-steps.py（context / finalize / remaining）。

gh は PATH の先頭に置いた偽物で置き換える。偽物は FAKE_GH_STATE の JSON を読み、呼ばれた引数を
calls に積む。`pr-body-decisions.sh` も偽物（FAKE_SYNC_CODE で終了コードを決める）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
PLUGIN_ROOT = SKILL.parents[1]
SCRIPT = SKILL / "scripts" / "fix-steps.py"
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
from step_result import validate_result  # noqa: E402

PY = sys.executable
PR = 812

FAKE_GH = r'''#!{py}
import json, os, sys
a = sys.argv[1:]
path = os.environ["FAKE_GH_STATE"]
st = json.load(open(path, encoding="utf-8"))
st.setdefault("calls", []).append(a)
json.dump(st, open(path, "w", encoding="utf-8"), ensure_ascii=False)
out, code = "", 0
if a[:2] == ["repo", "view"]:
    out = "o/r"
elif a[:2] == ["pr", "view"]:
    out = json.dumps(st["pr"])
elif a[:2] == ["api", "graphql"]:
    out = json.dumps({{"data": {{"repository": {{"pullRequest": {{"reviewThreads": {{"nodes": st["threads"]}}}}}}}}}})
elif a[:1] == ["api"]:
    ep = a[1]
    key = "inline" if ep.endswith("/comments") and "/pulls/" in ep else "reviews" if ep.endswith("/reviews") else "issue"
    out = json.dumps(st.get(key, []))
elif a[:2] == ["pr", "checks"]:
    out = json.dumps(st.get("checks", []))
elif a[:2] == ["run", "list"]:
    out = st.get("run_id", "")
elif a[:2] == ["run", "view"]:
    out = st.get("log", "")
else:
    code = 1
sys.stdout.write(out)
sys.exit(code)
'''

FAKE_SYNC = '''#!/usr/bin/env bash
echo "sync $*" >> "$FAKE_SYNC_LOG"
exit "${FAKE_SYNC_CODE:-0}"
'''


def _threads():
    return [
        {"id": "PRRT_1", "isResolved": False, "path": "a.py", "line": 3,
         "comments": {"nodes": [{"databaseId": 11, "body": "[major / logic] 空を弾く\n詳細", "author": {"login": "bot"}}]}},
        {"id": "PRRT_2", "isResolved": True, "path": "a.py", "line": 9,
         "comments": {"nodes": [{"databaseId": 12, "body": "済み", "author": {"login": "bot"}}]}},
        {"id": "PRRT_3", "isResolved": False, "path": "b.py", "line": 1,
         "comments": {"nodes": [{"databaseId": 13, "body": "[nit / style] 末尾", "author": {"login": "bot"}}]}},
    ]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH.format(py=PY), encoding="utf-8")
    gh.chmod(0o755)
    sync = bin_dir / "pr-body-decisions.sh"
    sync.write_text(FAKE_SYNC, encoding="utf-8")
    sync.chmod(0o755)
    state = tmp_path / "gh-state.json"
    state.write_text(json.dumps({
        "pr": {"number": PR, "url": "https://x/pull/812", "headRefName": "feat/x", "reviewDecision": "CHANGES_REQUESTED",
               "state": "OPEN", "body": "# 目的\n\n直す\n\n## やらないこと\n\n- 型の付け直し\n\n## 手順\n\n1"},
        "threads": _threads(),
        "inline": [{"path": "a.py", "line": 3, "user": {"login": "bot"}, "body": "空を弾く"}],
        "reviews": [{"body": "全体の所感", "state": "COMMENTED", "user": {"login": "bot"}}],
        "issue": [{"body": "PR コメント", "user": {"login": "u"}}],
        "checks": [{"name": "lint", "state": "FAILURE", "link": "https://x/1"},
                   {"name": "test", "state": "SUCCESS", "link": "https://x/2"}],
        "run_id": "555", "log": "E lint failed",
    }), encoding="utf-8")
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_STATE", str(state))
    monkeypatch.setenv("FAKE_SYNC_LOG", str(tmp_path / "sync.log"))
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp))
    return {"tmp": tmp, "state": state, "sync": sync, "root": tmp_path}


def run(*args, cwd=None):
    p = subprocess.run([PY, str(SCRIPT), *map(str, args)], capture_output=True, text=True, cwd=cwd)
    out = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else None
    if out is not None:
        assert validate_result(out, p.returncode) == [], (out, p.returncode)
    return p.returncode, out, p.stderr


def _git_repo(path: Path) -> str:
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "--allow-empty", "-m", "c"],
                   cwd=path, check=True)
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=path, capture_output=True, text=True).stdout.strip()


# --- context --------------------------------------------------------------------

def test_context_collects_threads_comments_ci_and_exclusions(env):
    code, out, err = run("context", PR)
    assert code == 0 and out["status"] == "ok", err
    assert out["metrics"] == {"pr": PR, "repo": "o/r", "unresolved": 2, "comments": 3, "ci_status": "FAILURE",
                              "ci_failed": 1, "excluded_sections": 1}
    ctx = Path(next(i["path"] for i in out["items"] if i["name"] == "context"))
    text = ctx.read_text(encoding="utf-8")
    assert "### やらないこと" in text and "型の付け直し" in text and "## 手順" not in text
    assert "`PRRT_1` comment_id=11 a.py:3" in text and "PRRT_2" not in text
    assert "[REVIEW-BODY]" in text and "[PR-COMMENT]" in text
    assert "- lint: FAILURE https://x/1" in text
    log = Path(next(i["path"] for i in out["items"] if i["name"] == "ci_log"))
    assert log.read_text(encoding="utf-8") == "E lint failed" and str(log) in text
    dec = json.loads(Path(next(i["path"] for i in out["items"] if i["name"] == "decisions")).read_text())
    assert dec["pr"] == PR and [d["thread_id"] for d in dec["decisions"]] == ["PRRT_1", "PRRT_3"]
    assert dec["decisions"][0]["summary"] == "[major / logic] 空を弾く" and dec["decisions"][0]["decision"] == ""
    calls = json.loads(env["state"].read_text())["calls"]
    assert not any(c[:2] == ["pr", "merge"] or "--watch" in c for c in calls)


def test_context_without_ci_failure_skips_log(env):
    st = json.loads(env["state"].read_text())
    st["checks"] = [{"name": "test", "state": "PENDING", "link": ""}]
    env["state"].write_text(json.dumps(st))
    code, out, _ = run("context", PR, "--repo", "o/r")
    assert code == 0 and out["metrics"]["ci_status"] == "PENDING"
    assert not any(i["name"] == "ci_log" for i in out["items"])
    calls = json.loads(env["state"].read_text())["calls"]
    assert not any(c[:2] == ["run", "list"] for c in calls)
    assert not any(c[:2] == ["repo", "view"] for c in calls)


# --- finalize -------------------------------------------------------------------

def _decisions(**over):
    d = {"pr": PR, "fix_commit": "abc1234", "ci_note": None, "decisions": [
        {"thread_id": "PRRT_1", "comment_id": 11, "path": "a.py", "line": 3, "severity": "major", "category": "logic",
         "summary": "空を弾く", "decision": "fixed", "reason": ""},
        {"thread_id": "PRRT_3", "comment_id": 13, "path": "b.py", "line": 1, "severity": "nit", "category": "style",
         "summary": "末尾", "decision": "deferred", "reason": "好みの範囲"},
        {"thread_id": "PRRT_4", "comment_id": 14, "path": "c.py", "line": 5, "severity": "minor", "category": "style",
         "summary": "heredoc", "decision": "rejected", "reason": "意図的な展開"},
        {"thread_id": "PRRT_5", "comment_id": 15, "path": "d.py", "line": 7, "severity": "minor", "category": "scope",
         "summary": "別の機能", "decision": "separate_pr", "reason": "", "issue": "#900"},
    ]}
    d.update(over)
    return d


def test_finalize_builds_merge_fix_contract(env):
    dec = env["tmp"] / "d.json"
    dec.write_text(json.dumps(_decisions()), encoding="utf-8")
    code, out, err = run("finalize", "--decisions", dec, "--sync-script", env["sync"])
    assert code == 0 and out["status"] == "ok", err
    res_path = env["tmp"] / f"fix-pr{PR}-result.json"
    assert next(i["path"] for i in out["items"] if i["name"] == "result") == str(res_path)
    res = json.loads(res_path.read_text())
    assert set(res) == {"pr", "fix_commit", "ci_status", "ci_failed_checks", "ci_note", "fixed_count", "by_severity",
                        "resolved_threads", "deferred", "rejected"}
    assert res["pr"] == PR and res["fix_commit"] == "abc1234" and res["fixed_count"] == 1
    assert res["by_severity"] == {"critical": 0, "major": 1, "minor": 0, "nit": 0}
    assert res["ci_status"] == "FAILURE" and res["ci_failed_checks"] == ["lint"]
    assert res["resolved_threads"] == [{"thread_id": "PRRT_1", "comment_id": 11, "path": "a.py", "line": 3}]
    assert [d["thread_id"] for d in res["deferred"]] == ["PRRT_3", "PRRT_5"]
    assert res["deferred"][0]["reason_for_deferral"] == "好みの範囲" and "resolve" not in res["deferred"][0]
    assert res["deferred"][1]["resolve"] is True and "#900" in res["deferred"][1]["reason_for_deferral"]
    assert res["rejected"][0]["reason_for_rejection"] == "意図的な展開" and res["rejected"][0]["severity"] == "minor"
    sync = next(i for i in out["items"] if i["name"] == "pr-body-decisions")
    assert sync == {"name": "pr-body-decisions", "result": "synced", "code": 0, "reason": ""}
    assert (env["root"] / "sync.log").read_text().strip() == f"sync sync {PR}"
    assert out["metrics"]["pr_body_decisions_code"] == 0
    assert "result_posts.py fix --pr 812 --result" in out["next"]


def test_finalize_output_passes_cross_review_normalizer(env):
    dec = env["tmp"] / "d.json"
    dec.write_text(json.dumps(_decisions()), encoding="utf-8")
    code, out, _ = run("finalize", "--decisions", dec, "--no-sync")
    assert code == 0
    spec = importlib.util.spec_from_file_location("cr_state", PLUGIN_ROOT / "skills" / "cross-review" / "scripts" / "state.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    res = json.loads((env["tmp"] / f"fix-pr{PR}-result.json").read_text())
    norm = mod._normalize_fix_result(res)
    assert norm["commit"] == "abc1234" and norm["fixed"] == 1 and norm["deferred"] == 2 and norm["rejected"] == 1
    assert [t["thread_id"] for t in norm["resolved_threads"]] == ["PRRT_1"]
    assert norm["ci"] == "FAILURE" and norm["ci_failed_checks"] == ["lint"]
    assert norm["by_severity"]["major"] == 1


def test_finalize_takes_commit_from_head_when_omitted(env):
    sha = _git_repo(env["root"] / "wt")
    dec = env["tmp"] / "d.json"
    dec.write_text(json.dumps(_decisions(fix_commit=None)), encoding="utf-8")
    code, out, _ = run("finalize", "--decisions", dec, "--no-sync", "--root", env["root"] / "wt")
    assert code == 0
    assert json.loads((env["tmp"] / f"fix-pr{PR}-result.json").read_text())["fix_commit"] == sha


def test_finalize_stops_on_invalid_decisions(env):
    d = _decisions()
    d["decisions"][0]["decision"] = ""
    d["decisions"][2].pop("line")
    d["decisions"][3]["issue"] = ""
    dec = env["tmp"] / "d.json"
    dec.write_text(json.dumps(d), encoding="utf-8")
    code, out, _ = run("finalize", "--decisions", dec, "--no-sync")
    assert code == 1 and out["status"] == "stopped"
    assert [i["index"] for i in out["items"]] == [0, 2, 3]
    assert "rejected には line が要る" in out["items"][1]["reason"]
    assert not (env["tmp"] / f"fix-pr{PR}-result.json").exists()


@pytest.mark.parametrize("sync_code,label,status,exit_code", [
    (1, "mismatch", "ok", 0), (2, "unreadable", "ok", 0), (3, "invalid_call", "stopped", 1)])
def test_finalize_reports_sync_exit_codes(env, monkeypatch, sync_code, label, status, exit_code):
    monkeypatch.setenv("FAKE_SYNC_CODE", str(sync_code))
    dec = env["tmp"] / "d.json"
    dec.write_text(json.dumps(_decisions()), encoding="utf-8")
    code, out, _ = run("finalize", "--decisions", dec, "--sync-script", env["sync"])
    assert (code, out["status"]) == (exit_code, status)
    sync = next(i for i in out["items"] if i["name"] == "pr-body-decisions")
    assert (sync["result"], sync["code"]) == (label, sync_code)
    assert (env["tmp"] / f"fix-pr{PR}-result.json").exists()


def test_finalize_missing_file_is_precondition(env):
    code, out, _ = run("finalize", "--decisions", env["tmp"] / "none.json")
    assert code == 3 and out["status"] == "stopped"


# --- remaining ------------------------------------------------------------------

def test_remaining_counts_only_threads_outside_deferred_and_rejected(env):
    res = env["tmp"] / f"fix-pr{PR}-result.json"
    res.write_text(json.dumps({"deferred": [{"thread_id": "PRRT_3", "reason_for_deferral": "x"}], "rejected": []}))
    code, out, _ = run("remaining", PR)
    assert code == 1 and out["status"] == "stopped"
    assert out["metrics"] == {"pr": PR, "unresolved": 2, "kept": 1, "leftover": 1}
    assert [(i["thread_id"], i["result"]) for i in out["items"]] == [("PRRT_1", "unresolved"), ("PRRT_3", "kept")]
    res.write_text(json.dumps({"deferred": [{"thread_id": "PRRT_3", "reason_for_deferral": "x"}],
                               "rejected": [{"thread_id": "PRRT_1", "reason_for_rejection": "y"}]}))
    code, out, _ = run("remaining", PR)
    assert code == 0 and out["status"] == "ok" and out["metrics"]["leftover"] == 0


def test_remaining_treats_resolved_separate_pr_as_leftover(env):
    res = env["tmp"] / f"fix-pr{PR}-result.json"
    res.write_text(json.dumps({"deferred": [{"thread_id": "PRRT_3", "resolve": True}, {"thread_id": "PRRT_1"}],
                               "rejected": []}))
    code, out, _ = run("remaining", PR)
    assert code == 1 and out["metrics"]["leftover"] == 1 and out["items"][1]["result"] == "unresolved"
