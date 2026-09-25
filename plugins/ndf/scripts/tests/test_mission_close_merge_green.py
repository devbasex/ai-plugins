"""mission-close.py と merged-steps.py merge-when-green、退避のファイルシステムまたぎ。

gh は PATH の先頭に置いた偽物で置き換える。偽物は FAKE_GH_STATE の JSON ファイルを読み書きし、
呼ばれた引数を calls に積む。
"""
from __future__ import annotations

import errno
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
from step_result import validate_result  # noqa: E402

PY = sys.executable

FAKE_GH = r'''#!{py}
import json, os, sys
a = sys.argv[1:]
path = os.environ["FAKE_GH_STATE"]
st = json.load(open(path, encoding="utf-8"))
st.setdefault("calls", []).append(a)
def save():
    json.dump(st, open(path, "w", encoding="utf-8"), ensure_ascii=False)
def opt(name):
    return a[a.index(name) + 1] if name in a else None
out, code = None, 0
if a[:2] == ["repo", "view"]:
    out = st.get("repo", "o/r") if "-q" in a else json.dumps({{"owner": {{"login": "o"}}, "name": "r"}})
elif a[:2] == ["pr", "view"]:
    n = a[2]
    if "body,comments" in a:
        rec = st.get("records", {{}}).get(n)
        out, code = (json.dumps(rec), 0) if rec is not None else (None, 1)
    elif "-q" in a:
        body = st.get("bodies", {{}}).get(n)
        out, code = (body, 0) if body is not None else (None, 1)
    else:
        seq = st.get("pr_seq", {{}}).get(n)
        if seq:
            cur = seq.pop(0) if len(seq) > 1 else seq[0]
            out = json.dumps(cur)
        else:
            code = 1
elif a[:2] == ["pr", "merge"]:
    code = st.get("merge_code", 0)
    if code == 0:
        for s in st.get("pr_seq", {{}}).get(a[2], []):
            s["state"] = "MERGED"
elif a[:2] == ["pr", "ready"]:
    code = st.get("ready_code", 0)
    if code == 0:
        for s in st.get("pr_seq", {{}}).get(a[2], []):
            s["isDraft"] = False
    else:
        sys.stderr.write("ready failed\n")
elif a[:2] == ["run", "view"]:
    seq = st.get("runs", {{}}).get(a[2])
    if seq:
        out = json.dumps(seq.pop(0) if len(seq) > 1 else seq[0])
    else:
        code = 1
elif a[:2] == ["run", "rerun"]:
    code = st.get("rerun_code", 0)
elif a[:2] == ["run", "list"]:
    out = json.dumps([{{"databaseId": i}} for i in range(st.get("queued_runs", 0))])
elif a[:2] == ["issue", "view"]:
    key = f"{{opt('--repo')}}#{{a[2]}}"
    seq = st.get("issues", {{}}).get(key)
    if seq is None:
        code = 1
    else:
        out = seq.pop(0) if len(seq) > 1 else seq[0]
        if out is None:
            code = 1
elif a[:2] == ["issue", "close"]:
    key = f"{{opt('--repo')}}#{{a[2]}}"
    st.setdefault("closed", []).append(key)
    seq = st.get("issues", {{}}).get(key)
    if seq is not None and st.get("close_works", True):
        st["issues"][key] = ["CLOSED"]
    out = "closed"
else:
    code = 1
save()
if out is not None:
    print(out)
sys.exit(code)
'''


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "develop")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "commit.gpgsign", "false")
    (root / "keep.txt").write_text("x\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def gh(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    f = bindir / "gh"
    f.write_text(FAKE_GH.format(py=PY), encoding="utf-8")
    f.chmod(0o755)
    state = tmp_path / "gh-state.json"
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["FAKE_GH_STATE"] = str(state)
    env["NDF_PRESENTATION_DIR"] = str(tmp_path / "pres")
    env["NDF_WORKTREE_BASE"] = str(tmp_path / "wtbase")
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(k, None)

    class G:
        def set(self, **kw):
            state.write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")

        def get(self):
            return json.loads(state.read_text(encoding="utf-8"))
    g = G()
    g.env = env
    g.set()
    return g


def call(script, args, env, cwd):
    p = subprocess.run([PY, str(SCRIPTS / script), *args], capture_output=True, text=True, env=env, cwd=cwd)
    lines = p.stdout.strip().splitlines()
    out = json.loads(lines[-1]) if lines else None
    if out is not None:
        assert validate_result(out, p.returncode) == [], (out, p.returncode)
    return p.returncode, out, p.stderr


# --- mission-close --------------------------------------------------------------

DIST_PROD = "## 配布の記録\n段階: 本番（2026-09-24 承認）\n版: 1.0.0 → 1.1.0（MINOR）\nミッション: PR #11 / #12\n"


def verify_block(ver, rows):
    table = "| 課題 | 受け入れ条件 | 実行したこと | 実行時刻 | 結果 |\n| --- | --- | --- | --- | --- |\n"
    table += "".join(f"| {k} | c | x | t | {r} |\n" for k, r in rows)
    return f"## リリース後テスト\n対象の版: {ver}（2026-09-24）\n\n{table}\n合否: 済\n"


def issues_of(out):
    return {f"{i['repo']}#{i['number']}": i for i in out["items"] if i["kind"] == "issue"}


def test_mission_close_four_outcomes(repo, gh):
    gh.set(records={"20": {"body": "old\n## 配布の記録\n段階: 検証\nミッション: PR #99\n",
                           "comments": [{"body": DIST_PROD}]}},
           bodies={"11": "Fixes #1, closes #2", "12": "Resolves #3 and fixes other/x#4"},
           issues={"o/r#1": ["OPEN"], "o/r#2": ["CLOSED"], "o/r#3": ["OPEN"], "other/x#4": [None]})
    code, out, err = call("mission-close.py", ["--record-pr", "20", "--repo", "o/r", "--label", "M26の振り返り"],
                          gh.env, repo)
    res = issues_of(out)
    assert res["o/r#1"]["result"] == "closed" and res["o/r#1"]["cmd"] == "gh issue reopen 1 --repo o/r"
    assert res["o/r#2"]["result"] == "already_closed"
    assert res["o/r#3"]["result"] == "closed"
    assert res["other/x#4"]["result"] == "failed" and "before" in res["other/x#4"]["reason"]
    assert code == 1 and out["status"] == "stopped" and out["tool"] == "mission-close"
    assert ["pr", "view", "99", "--repo", "o/r", "--json", "body", "-q", ".body"] not in gh.get()["calls"]


def test_mission_close_failed_when_close_does_not_take(repo, gh):
    gh.set(records={"20": {"body": DIST_PROD, "comments": []}}, bodies={"11": "Fixes #1", "12": ""},
           issues={"o/r#1": ["OPEN"]}, close_works=False)
    code, out, err = call("mission-close.py", ["--record-pr", "20", "--repo", "o/r"], gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert code == 1 and it["result"] == "failed" and it["cmd"] == "gh issue close 1 --repo o/r"


def test_mission_close_kept_open_before_production(repo, gh):
    gh.set(records={"20": {"body": "## 配布の記録\n段階: 検証（止めた）\n版: 1.0.0 → 1.1.0-dev.1\nミッション: PR #11\n",
                           "comments": []}},
           bodies={"11": "Fixes #1"}, issues={"o/r#1": ["OPEN"]})
    code, out, err = call("mission-close.py", ["--record-pr", "20", "--repo", "o/r"], gh.env, repo)
    assert code == 0 and out["status"] == "ok"
    assert issues_of(out)["o/r#1"]["result"] == "kept_open"
    assert "closed" not in gh.get()


def test_mission_close_verification_per_issue(repo, gh):
    body = DIST_PROD + "\n" + verify_block("1.1.0-dev.1", [("#1", "不合格")])
    gh.set(records={"20": {"body": body, "comments": [
        {"body": verify_block("1.1.0", [("#1", "合格"), ("#1", "合格（再試行）"), ("#2", "保留")])}]}},
        bodies={"11": "Fixes #1", "12": "Fixes #2 fixes #3"},
        issues={"o/r#1": ["OPEN"], "o/r#2": ["OPEN"], "o/r#3": ["OPEN"]})
    code, out, err = call("mission-close.py", ["--record-pr", "20", "--repo", "o/r", "--with-verification"],
                          gh.env, repo)
    res = issues_of(out)
    assert res["o/r#1"]["result"] == "closed"
    assert res["o/r#2"]["result"] == "kept_open" and "合格でない" in res["o/r#2"]["reason"]
    assert res["o/r#3"]["result"] == "kept_open" and "行が無い" in res["o/r#3"]["reason"]
    assert code == 0 and gh.get()["closed"] == ["o/r#1"]


def test_mission_close_no_verification_record_keeps_all(repo, gh):
    gh.set(records={"20": {"body": DIST_PROD, "comments": []}},
           bodies={"11": "Fixes #1", "12": ""}, issues={"o/r#1": ["OPEN"]})
    code, out, err = call("mission-close.py", ["--record-pr", "20", "--repo", "o/r", "--with-verification"],
                          gh.env, repo)
    assert code == 0 and issues_of(out)["o/r#1"]["reason"] == "本番の版のリリース後テストの記録が無い"


def test_mission_close_empty_list_is_2(repo, gh):
    gh.set(records={"20": {"body": "## 配布の記録\n段階: 配布なし（文書だけ）\n", "comments": []}})
    code, out, err = call("mission-close.py", ["--record-pr", "20", "--repo", "o/r"], gh.env, repo)
    assert code == 2 and out["status"] == "stopped"


def test_mission_close_dry_run_writes_nothing(repo, gh):
    gh.set(records={"20": {"body": "## 配布の記録\n段階: 配布なし（文書だけ）\n", "comments": []}},
           bodies={"11": "Fixes #1"}, issues={"o/r#1": ["OPEN"]})
    code, out, err = call("mission-close.py", ["--record-pr", "20", "--repo", "o/r", "--prs", "11", "--dry-run"],
                          gh.env, repo)
    assert code == 0 and issues_of(out)["o/r#1"]["result"] == "would_close"
    assert "closed" not in gh.get()


MULTI_GENERATION_RECORD = """## 配布の記録

段階: 本番（2026-09-01 10:00 に承認）
版: 10.13.0 → 10.14.0（MINOR: 旧ミッション）
ミッション: PR #700

## リリース後テスト

対象の版: 10.14.0（2026-09-01 11:00）
合否: 合格（旧版）

## 配布の記録

段階: 本番（2026-09-18 10:00 に承認）
版: 10.14.0 → 10.15.0（MINOR: 新ミッション）
ミッション: PR #717 / #718

## リリース後テスト

対象の版: 10.15.0（2026-09-18 11:00）
合否: 合格（同版の先行記録）

## リリース後テスト

対象の版: 10.15.0-dev.1（2026-09-18 12:00）
合否: 合格（異なる版）

## リリース後テスト

対象の版: 10.15.0（2026-09-18 13:00）
合否: 合格（選ぶ記録）
"""


def test_parse_record_selects_latest_distribution_and_matching_release_test():
    spec = importlib.util.spec_from_file_location("mission_close", SCRIPTS / "mission-close.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rec = mod.parse_record(MULTI_GENERATION_RECORD)
    assert rec["stage"] == "本番（2026-09-18 10:00 に承認）"
    assert rec["version"] == "10.15.0" and rec["mission_prs"] == [717, 718]
    assert rec["verify_block"].endswith("合否: 合格（選ぶ記録）")


def load_mission_close():
    spec = importlib.util.spec_from_file_location("mission_close", SCRIPTS / "mission-close.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_record_reads_old_list_line():
    """旧い記録の `まとまり:` の行も PR の一覧として読む。"""
    rec = load_mission_close().parse_record("## 配布の記録\n段階: 配布なし\nまとまり: PR #5 / #6\n")
    assert rec["mission_prs"] == [5, 6]


def test_old_name_passes_through_to_new_name(repo, gh):
    """旧名の bundle-close.py は案内を stderr に出し、引数をそのまま mission-close.py へ渡す。"""
    gh.set(records={"20": {"body": DIST_PROD, "comments": []}}, bodies={"11": "", "12": ""})
    code, out, err = call("bundle-close.py", ["--record-pr", "20", "--repo", "o/r", "--dry-run"], gh.env, repo)
    assert "mission-close.py" in err
    assert out["tool"] == "mission-close"
    p = subprocess.run([PY, str(SCRIPTS / "bundle-close.py")], capture_output=True, text=True, env=gh.env, cwd=repo)
    assert p.returncode == 3


def test_mission_close_bad_call_is_3(repo, gh):
    p = subprocess.run([PY, str(SCRIPTS / "mission-close.py")], capture_output=True, text=True, env=gh.env, cwd=repo)
    assert p.returncode == 3


# --- merge-when-green ----------------------------------------------------------

def run_(n, conclusion="SUCCESS", status="COMPLETED"):
    return {"__typename": "CheckRun", "name": n, "status": status, "conclusion": conclusion}


def test_merge_when_green_rewaits_on_push_and_merges(repo, gh):
    seq = [
        {"state": "OPEN", "headRefOid": "aaa", "statusCheckRollup": [run_("t", None, "IN_PROGRESS")]},
        {"state": "OPEN", "headRefOid": "bbb", "statusCheckRollup": [run_("t")]},
        {"state": "OPEN", "headRefOid": "bbb", "statusCheckRollup": [run_("t")]},
    ]
    merged = {"headRefName": "feat/x", "state": "MERGED", "mergeCommit": {"oid": "c"}}
    gh.set(pr_seq={"5": seq + [merged]})
    git(repo, "checkout", "-q", "-b", "other")  # 上流が無いので取り込みを行わせない
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0", "--root", str(repo)],
                          gh.env, repo)
    assert code == 0, (out, err)
    kinds = [(i["kind"], i["result"]) for i in out["items"]]
    assert ("restart", "rewait") in kinds and ("pr", "merged") in kinds
    merges = [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]
    assert merges == [["pr", "merge", "5", "--admin", "--merge"]]


def pr_views(gh):
    return [c for c in gh.get()["calls"] if c[:2] == ["pr", "view"]]


def pending_pr(sha="a"):
    return {"state": "OPEN", "headRefOid": sha, "statusCheckRollup": [run_("t", None, "IN_PROGRESS")]}


def passed_pr(sha="a", rollup=None):
    return {"state": "OPEN", "headRefOid": sha, "statusCheckRollup": [run_("t")] if rollup is None else rollup}


def test_merge_when_green_merges_on_first_green_after_pending(repo, gh):
    """同じ先頭のコミットで pending を見た後に全部が通ったら、確かめ直さずにその周でマージする。"""
    gh.set(pr_seq={"5": [pending_pr(), passed_pr(), passed_pr()]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup"],
                          gh.env, repo)
    assert code == 0, (out, err)
    assert ("pr", "merged") in [(i["kind"], i["result"]) for i in out["items"]]
    assert len(pr_views(gh)) == 2


def test_merge_when_green_rechecks_once_with_short_interval(repo, gh):
    """pending を見ずに通っていたら、--recheck の短い間隔で 1 度だけ確かめ直してマージする（--interval は待たない）。"""
    gh.set(pr_seq={"5": [passed_pr(), passed_pr(), passed_pr()]})
    started = time.monotonic()
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "60", "--recheck", "0",
                                              "--no-cleanup"], gh.env, repo)
    assert code == 0, (out, err)
    assert time.monotonic() - started < 30
    assert len(pr_views(gh)) == 2
    assert [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def test_merge_when_green_does_not_merge_while_rollup_is_empty(repo, gh):
    """rollup が空のうちは検査が載る前かもしれないのでマージしない。"""
    gh.set(pr_seq={"5": [passed_pr(rollup=[])]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0",
                                              "--timeout", "0"], gh.env, repo)
    assert code == 1 and out["status"] == "stopped"
    assert not [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def test_merge_when_green_waits_for_checks_to_appear(repo, gh):
    """空の rollup の後に検査が載って通れば、その通過でマージする。"""
    gh.set(pr_seq={"5": [passed_pr(rollup=[]), pending_pr(), passed_pr(), passed_pr()]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup"],
                          gh.env, repo)
    assert code == 0, (out, err)
    assert len(pr_views(gh)) == 3


def test_merge_when_green_treats_long_empty_rollup_as_no_ci(repo, gh):
    """rollup が --no-checks-after 秒を過ぎても空なら、CI の無いリポジトリとしてマージする。"""
    gh.set(pr_seq={"5": [passed_pr(rollup=[])]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-checks-after", "0",
                                              "--no-cleanup"], gh.env, repo)
    assert code == 0, (out, err)
    assert [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


STUCK_URL = "https://github.com/o/r/actions/runs/100/job/200"
MERGED = {"headRefName": "feat/x", "state": "MERGED", "mergeCommit": {"oid": "c"}}


def stuck_pr(status="IN_PROGRESS"):
    return {"state": "OPEN", "headRefOid": "a",
            "statusCheckRollup": [{**run_("build", None, status), "detailsUrl": STUCK_URL}]}


def green_pr():
    return {"state": "OPEN", "headRefOid": "a", "statusCheckRollup": [run_("build")]}


def reruns(gh):
    return [c for c in gh.get()["calls"] if c[:2] == ["run", "rerun"]]


def test_merge_when_green_reruns_stuck_check_once_then_merges(repo, gh):
    """実行が completed なのにチェックが pending なら、ジョブを 1 度だけ再実行し、通ればマージする。"""
    gh.set(pr_seq={"5": [stuck_pr(), green_pr(), green_pr(), MERGED]},
           runs={"100": [{"status": "completed", "jobs": [{"databaseId": 200, "status": "in_progress"}]}]})
    git(repo, "checkout", "-q", "-b", "other")
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--stale-after", "0",
                                              "--root", str(repo)], gh.env, repo)
    assert code == 0, (out, err)
    assert {"kind": "check", "name": "build", "result": "rerun", "run": "100", "job": "200"} in out["items"]
    assert reruns(gh) == [["run", "rerun", "100", "--job", "200"]]
    assert [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def test_merge_when_green_takes_conclusion_of_stuck_check_that_finished(repo, gh):
    """実行が completed でジョブに結論があれば、チェックの表示が pending のままでも結論で扱う（待たない・再実行しない）。"""
    gh.set(pr_seq={"5": [stuck_pr(), stuck_pr(), stuck_pr(), MERGED]},
           runs={"100": [{"status": "completed", "conclusion": "success",
                          "jobs": [{"databaseId": 200, "status": "in_progress", "conclusion": "success"}]}]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0",
                                              "--no-cleanup"], gh.env, repo)
    assert code == 0, (out, err)
    assert {"kind": "check", "name": "build", "result": "settled", "run": "100", "job": "200",
            "conclusion": "success"} in out["items"]
    assert reruns(gh) == []
    assert [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def test_merge_when_green_stops_when_stuck_check_finished_as_failure(repo, gh):
    """取り残されたチェックのジョブの結論が failure なら、失敗として止まる。"""
    gh.set(pr_seq={"5": [stuck_pr()]},
           runs={"100": [{"status": "completed", "conclusion": "failure",
                          "jobs": [{"databaseId": 200, "status": "in_progress", "conclusion": "failure"}]}]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup"],
                          gh.env, repo)
    assert code == 1 and out["status"] == "stopped"
    assert "失敗" in out["summary"] and "build" in out["summary"]
    assert reruns(gh) == []
    assert not [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def test_merge_when_green_waits_stale_after_before_rerun(repo, gh):
    """取り残しが --stale-after に満たないうちは再実行しない。"""
    gh.set(pr_seq={"5": [stuck_pr(), green_pr(), green_pr(), MERGED]},
           runs={"100": [{"status": "completed", "jobs": [{"databaseId": 200, "status": "in_progress"}]}]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup"],
                          gh.env, repo)
    assert code == 0, (out, err)
    assert reruns(gh) == []


def test_merge_when_green_stops_when_rerun_check_stays_stuck(repo, gh):
    """再実行した同じチェックが再び取り残されたら stopped で止まり、マージしない。"""
    gh.set(pr_seq={"5": [stuck_pr()]},
           runs={"100": [{"status": "completed", "jobs": [{"databaseId": 200, "status": "in_progress"}]}]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--stale-after", "0"],
                          gh.env, repo)
    assert code == 1 and out["status"] == "stopped"
    assert "再実行でも動かない" in out["summary"]
    assert [i["result"] for i in out["items"] if i["kind"] == "check"] == ["rerun", "stuck"]
    assert len(reruns(gh)) == 1
    assert not [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def test_merge_when_green_reports_runner_queue(repo, gh):
    """ジョブが queued の間は待ち行列の件数を stderr へ出し、metrics の queued_runs に残す。"""
    gh.set(pr_seq={"5": [stuck_pr("QUEUED"), stuck_pr("QUEUED"), green_pr(), green_pr(), MERGED]},
           runs={"100": [{"status": "queued", "jobs": [{"databaseId": 200, "status": "queued"}]}]},
           queued_runs=9)
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--stale-after", "0",
                                              "--no-cleanup"], gh.env, repo)
    assert code == 0, (out, err)
    assert err.count("merge-when-green: CI のランナー待ち（待ち行列 9 件、待ち 1 件）") == 2
    assert out["metrics"]["queued_runs"] == 9
    assert reruns(gh) == []


def test_merge_when_green_stops_on_failure(repo, gh):
    gh.set(pr_seq={"5": [{"state": "OPEN", "headRefOid": "a",
                          "statusCheckRollup": [run_("t", "FAILURE"),
                                                {"__typename": "StatusContext", "context": "s", "state": "SUCCESS"}]}]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0"], gh.env, repo)
    assert code == 1 and out["status"] == "stopped" and "t" in out["summary"]
    assert not [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def test_merge_when_green_timeout_stops(repo, gh):
    gh.set(pr_seq={"5": [{"state": "OPEN", "headRefOid": "a", "statusCheckRollup": [run_("t", None, "QUEUED")]}]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--timeout", "0"],
                          gh.env, repo)
    assert code == 1 and out["metrics"]["pending"] == 1


def test_merge_when_green_merge_failure_stops(repo, gh):
    gh.set(merge_code=1, pr_seq={"5": [{"state": "OPEN", "headRefOid": "a", "statusCheckRollup": [run_("t")]}]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0"], gh.env, repo)
    assert code == 1 and "gh pr merge" in out["summary"]


def test_merge_when_green_readies_draft_before_merge(repo, gh):
    draft = {"state": "OPEN", "isDraft": True, "headRefOid": "a", "statusCheckRollup": [run_("t")]}
    merged = {"headRefName": "feat/x", "state": "MERGED", "mergeCommit": {"oid": "c"}}
    gh.set(pr_seq={"5": [draft, dict(draft), dict(draft), merged]})
    git(repo, "checkout", "-q", "-b", "other")
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0", "--root", str(repo)],
                          gh.env, repo)
    assert code == 0, (out, err)
    assert [i for i in out["items"] if i["kind"] == "pr"] == [
        {"kind": "pr", "name": "#5", "result": "ready"},
        {"kind": "pr", "name": "#5", "result": "merged", "method": "merge"}]
    calls = [c[:2] for c in gh.get()["calls"] if c[0] == "pr" and c[1] in ("ready", "merge")]
    assert calls == [["pr", "ready"], ["pr", "merge"]]


def test_merge_when_green_not_draft_does_not_ready(repo, gh):
    gh.set(pr_seq={"5": [{"state": "OPEN", "isDraft": False, "headRefOid": "a", "statusCheckRollup": [run_("t")]}]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0", "--no-cleanup"],
                          gh.env, repo)
    assert code == 0, (out, err)
    assert not [c for c in gh.get()["calls"] if c[:2] == ["pr", "ready"]]


def test_merge_when_green_ready_failure_stops(repo, gh):
    gh.set(ready_code=1, pr_seq={"5": [{"state": "OPEN", "isDraft": True, "headRefOid": "a",
                                        "statusCheckRollup": [run_("t")]}]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0"], gh.env, repo)
    assert code == 1 and "gh pr ready" in out["summary"]
    assert not [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


# --- 退避がファイルシステムをまたぐ ----------------------------------------------

def test_evacuate_survives_cross_device(repo, tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("merged_steps", SCRIPTS / "merged-steps.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "feat/y", str(wt))
    (wt / "untracked.txt").write_text("u\n", encoding="utf-8")
    (wt / "dir").mkdir()
    (wt / "dir" / "f.txt").write_text("f\n", encoding="utf-8")
    real_rename = os.rename

    def cross(src, dst, *a, **k):
        raise OSError(errno.EXDEV, "Invalid cross-device link")
    monkeypatch.setattr(os, "rename", cross)
    monkeypatch.setattr(os, "replace", cross)
    trash = Path(mod.evacuate(str(wt), "feat/y"))
    monkeypatch.setattr(os, "rename", real_rename)
    assert (trash / "untracked.txt").read_text(encoding="utf-8") == "u\n"
    assert (trash / "dir" / "f.txt").is_file()
    assert not (wt / "untracked.txt").exists()
