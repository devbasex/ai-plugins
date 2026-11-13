"""sprint-close.py と merged-steps.py merge-when-green、退避のファイルシステムまたぎ。

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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import inproc  # noqa: E402

sys.path.insert(0, str(SCRIPTS / "lib"))
import approved_commit  # noqa: E402
from step_result import validate_result  # noqa: E402

PY = sys.executable

FAKE_GH = r"""#!{py}
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
    elif "title,body" in a:
        body = st.get("bodies", {{}}).get(n)
        title = st.get("titles", {{}}).get(n, "")
        out, code = (json.dumps({{"title": title, "body": body}}), 0) if body is not None else (None, 1)
    elif "-q" in a:
        body = st.get("bodies", {{}}).get(n)
        out, code = (body, 0) if body is not None else (None, 1)
    else:
        seq = st.get("pr_seq", {{}}).get(n)
        if seq:
            cur = seq.pop(0) if len(seq) > 1 else seq[0]
            out = json.dumps({{"baseRefName": "develop", **cur}})  # 宛先は本番チャネル（main）でない既定
        else:
            code = 1
elif a[:2] == ["pr", "merge"]:
    code = st.get("merge_code", 0)
    if code == 0:
        for s in st.get("pr_seq", {{}}).get(a[2], []):
            s["state"] = "MERGED"
    else:
        sys.stderr.write(st.get("merge_err", "merge failed") + "\n")
elif a[:2] == ["pr", "list"]:
    out = json.dumps(st.get("open_prs", []))
elif a[:2] == ["pr", "create"]:
    code = st.get("create_code", 0)
    if code == 0:
        out = "https://github.com/o/r/pull/" + str(st.get("create_number", 77))
    else:
        sys.stderr.write(st.get("create_err", "create failed") + "\n")
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
        elif "state,body" in a:
            out = json.dumps({{"state": out, "body": st.get("issue_bodies", {{}}).get(key, "")}})
elif a[:3] == ["api", "-X", "PATCH"]:
    m = __import__("re").match(r"repos/(.+)/issues/(\d+)$", a[4])
    key = f"{{m.group(1)}}#{{m.group(2)}}"
    if st.get("write_code", 0):
        code = 1
        sys.stderr.write("write failed\n")
    else:
        st.setdefault("issue_bodies", {{}})[key] = json.load(sys.stdin)["body"]
        st.setdefault("written", []).append(key)
        out = "HTTP/2.0 200 OK\n\n{{}}"
elif a[:3] == ["api", "-X", "POST"] and a[4].endswith("/comments"):  # 承認の証跡のコメント（#2773）
    st.setdefault("comments", []).append(json.load(sys.stdin)["body"])
    out = 'HTTP/2.0 201 Created\n\n{{"html_url": "https://github.com/o/r/pull/9#c1"}}'
elif a[:1] == ["api"] and "rules/branches/" in a[-1]:
    out = json.dumps(st.get("rules", []))
elif a[:1] == ["api"] and a[-1].endswith("/status"):  # 先端の commit status（ndf/release-approval の待ち）
    out = json.dumps({{"statuses": st.get("statuses", [])}})
elif a[:1] == ["api"] and "/protection/" in a[-1]:
    code = 1
    sys.stderr.write("gh: Not Found (HTTP 404)\n")
elif a[:2] == ["api", "user"]:
    out = st.get("login", "me")
elif a[:1] == ["api"] and "/comments" in " ".join(a):
    out = "\n".join(json.dumps(c) for c in st.get("issue_comments", []))
elif a[:1] == ["api"] and "reviews" in st and any("/reviews?" in x for x in a):  # キーが無ければ従来どおり読めない
    out = "\n".join(json.dumps(r) for r in st["reviews"])
elif a[:1] == ["api"] and a[-1].endswith("/permission") and a[-1].split("/")[-2] in st.get("perms", {{}}):
    p = st["perms"][a[-1].split("/")[-2]]
    out = json.dumps({{"permission": p, "role_name": p}})
elif a[:1] == ["api"] and __import__("re").search(r"/pulls/\d+$", a[-1]):
    seq = st.get("pr_seq", {{}}).get(a[-1].rsplit("/", 1)[1]) or [{{}}]
    cur = seq[0]
    out = json.dumps({{"head": {{"sha": cur.get("headRefOid", "")}}, "base": {{"ref": cur.get("baseRefName", "main")}}, "state": "open"}})
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
"""


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
    git(root, "branch", "main")  # 本番チャネル（既定ブランチ）。develop 宛ての PR はリリース承認ゲートに当たらない
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
    env["TMPDIR"] = str((tmp_path / "systmp").resolve())  # 片付け（run_state.sweep_states）が手元の一時の置き場に触れない
    (tmp_path / "systmp").mkdir(exist_ok=True)
    env["NDF_LOCK_DIR"] = str(tmp_path / "locks")
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GH_TOKEN", "GITHUB_TOKEN"):  # REST も偽の gh を通す
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
    p = inproc.run(SCRIPTS / script, *args, env=env, cwd=cwd)
    lines = p.stdout.strip().splitlines()
    out = json.loads(lines[-1]) if lines else None
    if out is not None:
        assert validate_result(out, p.returncode) == [], (out, p.returncode)
    return p.returncode, out, p.stderr


# --- sprint-close --------------------------------------------------------------

DIST_PROD = "## リリース記録\n段階: 本番（2026-09-24 承認）\n版: 1.0.0 → 1.1.0（MINOR）\nスプリント: PR #11 / #12\n"


def verify_block(ver, rows):
    table = "| 課題 | 受け入れ条件 | 実行したこと | 実行時刻 | 結果 |\n| --- | --- | --- | --- | --- |\n"
    table += "".join(f"| {k} | c | x | t | {r} |\n" for k, r in rows)
    return f"## リリース後テスト\n対象の版: {ver}（2026-09-24）\n\n{table}\n合否: 済\n"


def issues_of(out):
    return {f"{i['repo']}#{i['number']}": i for i in out["items"] if i["kind"] == "issue"}


def test_sprint_close_four_outcomes(repo, gh):
    gh.set(
        records={"20": {"body": "old\n## リリース記録\n段階: 検証\nスプリント: PR #99\n", "comments": [{"body": DIST_PROD}]}},
        bodies={"11": "Fixes #1, closes #2", "12": "Resolves #3 and fixes other/x#4"},
        issues={"o/r#1": ["OPEN"], "o/r#2": ["CLOSED"], "o/r#3": ["OPEN"], "other/x#4": [None]},
    )
    code, out, err = call(
        "sprint-close.py", ["--final-stage", "デプロイ", "--record-pr", "20", "--repo", "o/r", "--label", "M26の振り返り"], gh.env, repo
    )
    res = issues_of(out)
    assert res["o/r#1"]["result"] == "closed" and res["o/r#1"]["cmd"] == "gh issue reopen 1 --repo o/r"
    assert res["o/r#2"]["result"] == "already_closed"
    assert res["o/r#3"]["result"] == "closed"
    assert res["other/x#4"]["result"] == "failed" and "before" in res["other/x#4"]["reason"]
    assert code == 1 and out["status"] == "stopped" and out["tool"] == "sprint-close"
    assert ["pr", "view", "99", "--repo", "o/r", "--json", "title,body"] not in gh.get()["calls"]


def test_sprint_close_failed_when_close_does_not_take(repo, gh):
    gh.set(
        records={"20": {"body": DIST_PROD, "comments": []}},
        bodies={"11": "Fixes #1", "12": ""},
        issues={"o/r#1": ["OPEN"]},
        close_works=False,
    )
    code, out, err = call("sprint-close.py", ["--final-stage", "デプロイ", "--record-pr", "20", "--repo", "o/r"], gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert code == 1 and it["result"] == "failed" and it["cmd"] == "gh issue close 1 --repo o/r"


def test_sprint_close_kept_open_before_production(repo, gh):
    gh.set(
        records={"20": {"body": "## リリース記録\n段階: 検証（止めた）\n版: 1.0.0 → 1.1.0-dev.1\nスプリント: PR #11\n", "comments": []}},
        bodies={"11": "Fixes #1"},
        issues={"o/r#1": ["OPEN"]},
    )
    code, out, err = call("sprint-close.py", ["--final-stage", "デプロイ", "--record-pr", "20", "--repo", "o/r"], gh.env, repo)
    assert code == 10 and out["status"] == "gate"
    assert issues_of(out)["o/r#1"]["result"] == "kept_open"
    assert "closed" not in gh.get()


def test_sprint_close_verification_per_issue(repo, gh):
    body = DIST_PROD + "\n" + verify_block("1.1.0-dev.1", [("#1", "不合格")])
    gh.set(
        records={
            "20": {"body": body, "comments": [{"body": verify_block("1.1.0", [("#1", "合格"), ("#1", "合格（再試行）"), ("#2", "保留")])}]}
        },
        bodies={"11": "Fixes #1", "12": "Fixes #2 fixes #3"},
        issues={"o/r#1": ["OPEN"], "o/r#2": ["OPEN"], "o/r#3": ["OPEN"]},
    )
    code, out, err = call(
        "sprint-close.py", ["--final-stage", "デプロイ", "--record-pr", "20", "--repo", "o/r", "--with-verification"], gh.env, repo
    )
    res = issues_of(out)
    assert res["o/r#1"]["result"] == "closed"
    assert res["o/r#2"]["result"] == "kept_open" and "合格でない" in res["o/r#2"]["reason"]
    assert res["o/r#3"]["result"] == "kept_open" and "行が無い" in res["o/r#3"]["reason"]
    assert code == 10 and gh.get()["closed"] == ["o/r#1"]


def test_sprint_close_no_verification_record_keeps_all(repo, gh):
    gh.set(records={"20": {"body": DIST_PROD, "comments": []}}, bodies={"11": "Fixes #1", "12": ""}, issues={"o/r#1": ["OPEN"]})
    code, out, err = call(
        "sprint-close.py", ["--final-stage", "デプロイ", "--record-pr", "20", "--repo", "o/r", "--with-verification"], gh.env, repo
    )
    assert code == 10 and issues_of(out)["o/r#1"]["reason"] == "本番の版のリリース後テストの記録が無い"


def test_sprint_close_empty_list_is_2(repo, gh):
    gh.set(records={"20": {"body": "## リリース記録\n段階: デプロイなし（文書だけ）\n", "comments": []}})
    code, out, err = call("sprint-close.py", ["--final-stage", "デプロイ", "--record-pr", "20", "--repo", "o/r"], gh.env, repo)
    assert code == 2 and out["status"] == "stopped"


def test_sprint_close_dry_run_writes_nothing(repo, gh):
    gh.set(
        records={"20": {"body": "## リリース記録\n段階: デプロイなし（文書だけ）\n", "comments": []}},
        bodies={"11": "Fixes #1"},
        issues={"o/r#1": ["OPEN"]},
    )
    code, out, err = call(
        "sprint-close.py", ["--final-stage", "デプロイ", "--record-pr", "20", "--repo", "o/r", "--prs", "11", "--dry-run"], gh.env, repo
    )
    assert code == 0 and issues_of(out)["o/r#1"]["result"] == "would_close"
    assert "closed" not in gh.get()


MULTI_GENERATION_RECORD = """## リリース記録

段階: 本番（2026-09-01 10:00 に承認）
版: 10.13.0 → 10.14.0（MINOR: 旧スプリント）
スプリント: PR #700

## リリース後テスト

対象の版: 10.14.0（2026-09-01 11:00）
合否: 合格（旧版）

## リリース記録

段階: 本番（2026-09-18 10:00 に承認）
版: 10.14.0 → 10.15.0（MINOR: 新スプリント）
スプリント: PR #717 / #718

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
    spec = importlib.util.spec_from_file_location("sprint_close", SCRIPTS / "sprint-close.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rec = mod.parse_record(MULTI_GENERATION_RECORD)
    assert rec["stage"] == "本番（2026-09-18 10:00 に承認）"
    assert rec["version"] == "10.15.0" and rec["sprint_prs"] == [717, 718]
    assert rec["verify_block"].endswith("合否: 合格（選ぶ記録）")


def load_sprint_close():
    spec = importlib.util.spec_from_file_location("sprint_close", SCRIPTS / "sprint-close.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sprint_close_bad_call_is_2(repo, gh):
    p = subprocess.run([PY, str(SCRIPTS / "sprint-close.py")], capture_output=True, text=True, env=gh.env, cwd=repo)
    assert p.returncode == 2


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
    code, out, err = call(
        "merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0", "--root", str(repo)], gh.env, repo
    )
    assert code == 0, (out, err)
    kinds = [(i["kind"], i["result"]) for i in out["items"]]
    assert ("restart", "rewait") in kinds and ("pr", "merged") in kinds
    merges = [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]
    assert merges == [["pr", "merge", "5", "--admin", "--merge", "--match-head-commit", "bbb"]]  # 緑を確かめた先頭に限る


def test_merge_when_green_ok_when_local_change_blocks_pull(repo, gh, tmp_path):
    # マージの後の取り込みが未コミットの変更に塞がれても、マージは済んでいるので ok・0 で終える（#1685）
    main = tmp_path / "main"
    git(tmp_path, "clone", "-q", "-b", "develop", str(repo), str(main))
    git(main, "remote", "set-head", "origin", "main")  # 既定ブランチは main。develop 宛てはリリース承認ゲートに当たらない
    (main / ".ndf").mkdir()
    (main / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": "develop"}), encoding="utf-8")
    (repo / "keep.txt").write_text("上流\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "up")
    (main / "keep.txt").write_text("手元\n", encoding="utf-8")
    merged = {"headRefName": "feat/x", "state": "MERGED", "mergeCommit": {"oid": "c"}}
    gh.set(pr_seq={"5": [{"state": "OPEN", "headRefOid": "a", "statusCheckRollup": [run_("t")]}] * 2 + [merged]})
    code, out, err = call(
        "merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0", "--root", str(main)], gh.env, main
    )
    assert code == 0 and out["status"] == "ok", (out, err)
    item = {(i["kind"], i["name"]): i for i in out["items"]}[("main_dir", str(main))]
    assert item["result"] == "kept" and "未コミットの変更" in item["reason"]
    assert (main / "keep.txt").read_text(encoding="utf-8") == "手元\n"
    assert git(main, "stash", "list") == ""


@pytest.mark.parametrize("expect, code", [("bbb", 0), ("aaa", 1)])
def test_merge_when_green_expect_head_merges_only_the_compared_head(repo, gh, expect, code):
    """#815 の I5: --expect-head の SHA が緑を確かめた先端と違えば、gh pr merge を呼ばずに head_moved で止まる。"""
    gh.set(pr_seq={"5": [passed_pr("bbb"), passed_pr("bbb")]})
    code_, out, err = call(
        "merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup", "--expect-head", expect], gh.env, repo
    )
    assert code_ == code, (out, err)
    merges = [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]
    if code == 0:
        assert merges == [["pr", "merge", "5", "--admin", "--merge", "--match-head-commit", "bbb"]]
    else:
        assert merges == [] and out["status"] == "stopped"
        assert {"kind": "pr", "name": "#5", "result": "head_moved", "head": "bbb", "expected": "aaa"} in out["items"]


def test_merge_when_green_expect_head_stops_when_merged_at_other_head(repo, gh):
    """#815 の I5: 待つ間に別の先端でマージされた PR も、成功にせず head_moved で止まる（読んだ直後に比べる）。"""
    merged = {"state": "MERGED", "headRefOid": "bbb", "mergeCommit": {"oid": "c"}}
    gh.set(pr_seq={"5": [merged]})
    code, out, err = call(
        "merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup", "--expect-head", "aaa"], gh.env, repo
    )
    assert code == 1 and out["status"] == "stopped", (out, err)
    assert {"kind": "pr", "name": "#5", "result": "head_moved", "head": "bbb", "expected": "aaa"} in out["items"]
    assert not [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def pr_views(gh):
    return [c for c in gh.get()["calls"] if c[:2] == ["pr", "view"]]


def pending_pr(sha="a"):
    return {"state": "OPEN", "headRefOid": sha, "statusCheckRollup": [run_("t", None, "IN_PROGRESS")]}


def passed_pr(sha="a", rollup=None):
    return {"state": "OPEN", "headRefOid": sha, "statusCheckRollup": [run_("t")] if rollup is None else rollup}


# --- #1917-4: ベースブランチとの衝突は終了コード 4 ---------------------------------


def test_merge_when_green_stops_with_4_when_the_pr_is_dirty(repo, gh):
    gh.set(pr_seq={"5": [{**passed_pr(), "mergeStateStatus": "DIRTY"}]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup"], gh.env, repo)
    assert code == 4 and out["status"] == "stopped", (out, err)
    assert {"kind": "pr", "name": "#5", "result": "conflict"} in out["items"]
    assert not [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def test_merge_when_green_waits_while_merge_state_is_unknown(repo, gh):
    """前提 4: UNKNOWN（GitHub が計算中）は衝突と読まずに待ち、通ればマージする。"""
    unknown = {**pending_pr(), "mergeStateStatus": "UNKNOWN"}
    gh.set(pr_seq={"5": [unknown, {**passed_pr(), "mergeStateStatus": "CLEAN"}, passed_pr()]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup"], gh.env, repo)
    assert code == 0 and ("pr", "merged") in [(i["kind"], i["result"]) for i in out["items"]], (out, err)


@pytest.mark.parametrize(
    "err_text, last, code",
    [
        ("GraphQL: Pull Request is not mergeable: the merge commit cannot be cleanly created", {}, 4),
        ("merge failed", {"mergeStateStatus": "DIRTY"}, 4),
        ("merge failed", {"mergeStateStatus": "BLOCKED"}, 1),
    ],
)
def test_merge_when_green_refused_merge_is_4_only_when_it_conflicts(repo, gh, err_text, last, code):
    """gh pr merge の拒否は、衝突の文言か読み直した DIRTY のときだけ 4。ほかの拒否は今までどおり 1。"""
    gh.set(pr_seq={"5": [pending_pr(), passed_pr(), {**passed_pr(), **last}]}, merge_code=1, merge_err=err_text)
    code_, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup"], gh.env, repo)
    assert code_ == code and out["status"] == "stopped", (out, err)
    assert ({"kind": "pr", "name": "#5", "result": "conflict"} in out["items"]) is (code == 4)


def test_merge_when_green_merges_on_first_green_after_pending(repo, gh):
    """同じ先頭のコミットで pending を見た後に全部が通ったら、確かめ直さずにその周でマージする。"""
    gh.set(pr_seq={"5": [pending_pr(), passed_pr(), passed_pr()]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup"], gh.env, repo)
    assert code == 0, (out, err)
    assert ("pr", "merged") in [(i["kind"], i["result"]) for i in out["items"]]
    assert len(pr_views(gh)) == 2


def test_merge_when_green_rechecks_once_with_short_interval(repo, gh):
    """pending を見ずに通っていたら、--recheck の短い間隔で 1 度だけ確かめ直してマージする（--interval は待たない）。"""
    gh.set(pr_seq={"5": [passed_pr(), passed_pr(), passed_pr()]})
    started = time.monotonic()
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "60", "--recheck", "0", "--no-cleanup"], gh.env, repo)
    assert code == 0, (out, err)
    assert time.monotonic() - started < 30
    assert len(pr_views(gh)) == 2
    assert [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def test_merge_when_green_does_not_merge_while_rollup_is_empty(repo, gh):
    """rollup が空のうちはチェックが載る前かもしれないのでマージしない。"""
    gh.set(pr_seq={"5": [passed_pr(rollup=[])]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0", "--timeout", "0"], gh.env, repo)
    assert code == 1 and out["status"] == "stopped"
    assert not [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def test_merge_when_green_waits_for_checks_to_appear(repo, gh):
    """空の rollup の後にチェックが載って通れば、その通過でマージする。"""
    gh.set(pr_seq={"5": [passed_pr(rollup=[]), pending_pr(), passed_pr(), passed_pr()]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup"], gh.env, repo)
    assert code == 0, (out, err)
    assert len(pr_views(gh)) == 3


def test_merge_when_green_treats_long_empty_rollup_as_no_ci(repo, gh):
    """rollup が --no-checks-after 秒を過ぎても空なら、CI の無いリポジトリとしてマージする。"""
    gh.set(pr_seq={"5": [passed_pr(rollup=[])]})
    code, out, err = call(
        "merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-checks-after", "0", "--no-cleanup"], gh.env, repo
    )
    assert code == 0, (out, err)
    assert [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


STUCK_URL = "https://github.com/o/r/actions/runs/100/job/200"
MERGED = {"headRefName": "feat/x", "state": "MERGED", "mergeCommit": {"oid": "c"}}


def stuck_pr(status="IN_PROGRESS"):
    return {"state": "OPEN", "headRefOid": "a", "statusCheckRollup": [{**run_("build", None, status), "detailsUrl": STUCK_URL}]}


def green_pr():
    return {"state": "OPEN", "headRefOid": "a", "statusCheckRollup": [run_("build")]}


def reruns(gh):
    return [c for c in gh.get()["calls"] if c[:2] == ["run", "rerun"]]


def test_merge_when_green_reruns_stuck_check_once_then_merges(repo, gh):
    """実行が completed なのにチェックが pending なら、ジョブを 1 度だけ再実行し、通ればマージする。"""
    gh.set(
        pr_seq={"5": [stuck_pr(), green_pr(), green_pr(), MERGED]},
        runs={"100": [{"status": "completed", "jobs": [{"databaseId": 200, "status": "in_progress"}]}]},
    )
    git(repo, "checkout", "-q", "-b", "other")
    code, out, err = call(
        "merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--stale-after", "0", "--root", str(repo)], gh.env, repo
    )
    assert code == 0, (out, err)
    assert {"kind": "check", "name": "build", "result": "rerun", "run": "100", "job": "200"} in out["items"]
    assert reruns(gh) == [["run", "rerun", "100", "--job", "200"]]
    assert [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def test_merge_when_green_takes_conclusion_of_stuck_check_that_finished(repo, gh):
    """実行が completed でジョブに結論があれば、チェックの表示が pending のままでも結論で扱う（待たない・再実行しない）。"""
    gh.set(
        pr_seq={"5": [stuck_pr(), stuck_pr(), stuck_pr(), MERGED]},
        runs={
            "100": [
                {
                    "status": "completed",
                    "conclusion": "success",
                    "jobs": [{"databaseId": 200, "status": "in_progress", "conclusion": "success"}],
                }
            ]
        },
    )
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0", "--no-cleanup"], gh.env, repo)
    assert code == 0, (out, err)
    assert {"kind": "check", "name": "build", "result": "settled", "run": "100", "job": "200", "conclusion": "success"} in out["items"]
    assert reruns(gh) == []
    assert [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def test_merge_when_green_stops_when_stuck_check_finished_as_failure(repo, gh):
    """取り残されたチェックのジョブの結論が failure なら、失敗として止まる。"""
    gh.set(
        pr_seq={"5": [stuck_pr()]},
        runs={
            "100": [
                {
                    "status": "completed",
                    "conclusion": "failure",
                    "jobs": [{"databaseId": 200, "status": "in_progress", "conclusion": "failure"}],
                }
            ]
        },
    )
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup"], gh.env, repo)
    assert code == 1 and out["status"] == "stopped"
    assert "失敗" in out["summary"] and "build" in out["summary"]
    assert reruns(gh) == []
    assert not [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def test_merge_when_green_waits_stale_after_before_rerun(repo, gh):
    """取り残しが --stale-after に満たないうちは再実行しない。"""
    gh.set(
        pr_seq={"5": [stuck_pr(), green_pr(), green_pr(), MERGED]},
        runs={"100": [{"status": "completed", "jobs": [{"databaseId": 200, "status": "in_progress"}]}]},
    )
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup"], gh.env, repo)
    assert code == 0, (out, err)
    assert reruns(gh) == []


def test_merge_when_green_stops_when_rerun_check_stays_stuck(repo, gh):
    """再実行した同じチェックが再び取り残されたら stopped で止まり、マージしない。"""
    gh.set(pr_seq={"5": [stuck_pr()]}, runs={"100": [{"status": "completed", "jobs": [{"databaseId": 200, "status": "in_progress"}]}]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--stale-after", "0"], gh.env, repo)
    assert code == 1 and out["status"] == "stopped"
    assert "再実行でも動かない" in out["summary"]
    assert [i["result"] for i in out["items"] if i["kind"] == "check"] == ["rerun", "stuck"]
    assert len(reruns(gh)) == 1
    assert not [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def test_merge_when_green_reports_runner_queue(repo, gh):
    """ジョブが queued の間は待ち行列の件数を stderr へ出し、metrics の queued_runs に残す。"""
    gh.set(
        pr_seq={"5": [stuck_pr("QUEUED"), stuck_pr("QUEUED"), green_pr(), green_pr(), MERGED]},
        runs={"100": [{"status": "queued", "jobs": [{"databaseId": 200, "status": "queued"}]}]},
        queued_runs=9,
    )
    code, out, err = call(
        "merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--stale-after", "0", "--no-cleanup"], gh.env, repo
    )
    assert code == 0, (out, err)
    assert err.count("merge-when-green: CI のランナー待ち（待ち行列 9 件、待ち 1 件）") == 2
    assert out["metrics"]["queued_runs"] == 9
    assert reruns(gh) == []


def test_merge_when_green_stops_on_failure(repo, gh):
    gh.set(
        pr_seq={
            "5": [
                {
                    "state": "OPEN",
                    "headRefOid": "a",
                    "statusCheckRollup": [run_("t", "FAILURE"), {"__typename": "StatusContext", "context": "s", "state": "SUCCESS"}],
                }
            ]
        }
    )
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0"], gh.env, repo)
    assert code == 1 and out["status"] == "stopped" and "t" in out["summary"]
    assert not [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def test_merge_when_green_timeout_stops(repo, gh):
    gh.set(pr_seq={"5": [{"state": "OPEN", "headRefOid": "a", "statusCheckRollup": [run_("t", None, "QUEUED")]}]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--timeout", "0"], gh.env, repo)
    assert code == 1 and out["metrics"]["pending"] == 1


def test_merge_when_green_merge_failure_stops(repo, gh):
    gh.set(merge_code=1, pr_seq={"5": [{"state": "OPEN", "headRefOid": "a", "statusCheckRollup": [run_("t")]}]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0"], gh.env, repo)
    assert code == 1 and "gh pr merge" in out["summary"]


def test_merge_when_green_refused_merge_points_to_superseded_runs(repo, gh):
    """AC4: マージが拒まれ、置き換わった失敗があれば、next に run ごとの gh run rerun が載る。"""
    old = {**run_("check", "FAILURE"), "workflowName": "PR body decisions", "startedAt": "2026-10-05T04:44:10Z"}
    old["detailsUrl"] = "https://github.com/o/r/actions/runs/37264758029/job/1"
    new = {**run_("check"), "workflowName": "PR body decisions", "startedAt": "2026-10-05T04:46:35Z"}
    gh.set(merge_code=1, pr_seq={"5": [{"state": "OPEN", "headRefOid": "a", "statusCheckRollup": [old, new]}]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0"], gh.env, repo)
    assert code == 1 and "gh pr merge" in out["summary"]
    assert out["next"] == "置き換わった失敗を通し直してから打ち直す: gh run rerun 37264758029; merged-steps.py merge-when-green 5"


def test_merge_when_green_readies_draft_before_merge(repo, gh):
    draft = {"state": "OPEN", "isDraft": True, "headRefOid": "a", "statusCheckRollup": [run_("t")]}
    merged = {"headRefName": "feat/x", "state": "MERGED", "mergeCommit": {"oid": "c"}}
    gh.set(pr_seq={"5": [draft, dict(draft), dict(draft), merged]})
    git(repo, "checkout", "-q", "-b", "other")
    code, out, err = call(
        "merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0", "--root", str(repo)], gh.env, repo
    )
    assert code == 0, (out, err)
    assert [i for i in out["items"] if i["kind"] == "pr"] == [
        {"kind": "pr", "name": "#5", "result": "ready"},
        {"kind": "pr", "name": "#5", "result": "merged", "method": "merge", "head": "a"},
    ]
    calls = [c[:2] for c in gh.get()["calls"] if c[0] == "pr" and c[1] in ("ready", "merge")]
    assert calls == [["pr", "ready"], ["pr", "merge"]]


def test_merge_when_green_not_draft_does_not_ready(repo, gh):
    gh.set(pr_seq={"5": [{"state": "OPEN", "isDraft": False, "headRefOid": "a", "statusCheckRollup": [run_("t")]}]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0", "--no-cleanup"], gh.env, repo)
    assert code == 0, (out, err)
    assert not [c for c in gh.get()["calls"] if c[:2] == ["pr", "ready"]]


def test_merge_when_green_ready_failure_stops(repo, gh):
    gh.set(ready_code=1, pr_seq={"5": [{"state": "OPEN", "isDraft": True, "headRefOid": "a", "statusCheckRollup": [run_("t")]}]})
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
    trash = Path(mod.trash.evacuate(str(wt), "feat/y")[0])
    monkeypatch.setattr(os, "rename", real_rename)
    assert (trash / "untracked.txt").read_text(encoding="utf-8") == "u\n"
    assert (trash / "dir" / "f.txt").is_file()
    assert not (wt / "untracked.txt").exists()


def load_merged():
    spec = importlib.util.spec_from_file_location("merged_steps", SCRIPTS / "merged-steps.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cleanup_result_splits_own_and_swept_by_mark_not_value():
    """同じ値の kept が自分の片付けと持ち主の終わった状態の片付けの両方にあっても、それぞれで数える。"""
    mod = load_merged()
    same = {"kind": "worktree", "name": "/w/pr1", "result": "kept", "reason": "r"}
    items = [dict(same), {**same, "swept": True}]
    _, _, _, metrics, _, _ = mod._cleanup_result(items, [1], None)
    assert (metrics["kept"], metrics["swept_kept"]) == (1, 1)


@pytest.mark.parametrize("change", ["modified", "staged", "deleted"])
def test_remove_worktree_keeps_uncommitted_tracked_changes(repo, tmp_path, change):
    """追跡ファイルの未コミットの変更は退避できないので、--force で消さず kept にする。"""
    mod = load_merged()
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "feat/z", str(wt))
    (wt / "untracked.txt").write_text("u\n", encoding="utf-8")
    if change == "deleted":
        (wt / "keep.txt").unlink()
    else:
        (wt / "keep.txt").write_text("edited\n", encoding="utf-8")
        if change == "staged":
            git(wt, "add", "keep.txt")
    ok, why = mod.remove_worktree(str(repo), str(wt), "feat/z")
    assert ok is False and "keep.txt" in why and "--force" in why
    assert wt.is_dir() and (wt / "untracked.txt").exists()
    if change != "deleted":
        assert (wt / "keep.txt").read_text(encoding="utf-8") == "edited\n"
    assert any(w["path"] == str(wt) for w in mod.list_worktrees(str(repo)))


def test_remove_worktree_evacuates_untracked_and_removes(repo, tmp_path):
    """未追跡だけなら退避してから外す（従来どおり）。"""
    mod = load_merged()
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "feat/w", str(wt))
    (wt / "untracked.txt").write_text("u\n", encoding="utf-8")
    ok, why = mod.remove_worktree(str(repo), str(wt), "feat/w")
    assert ok is True and why.startswith("退避先 ")
    assert not wt.exists()
    assert (Path(why[len("退避先 ") :]) / "untracked.txt").read_text(encoding="utf-8") == "u\n"


def test_parse_record_skips_a_distribution_heading_inside_a_fence():
    """lib/md.py の上で読む（#1142 の D1）: 囲みの中の `## リリース記録` は記録として読まない。"""
    text = "## リリース記録\n段階: デプロイなし\nスプリント: #5\n\nコメント\n\n```md\n## リリース記録\n段階: 開発版\n```\n"
    rec = load_sprint_close().parse_record(text)
    assert rec["stage"] == "デプロイなし"
    assert rec["sprint_prs"] == [5]


def test_verification_verdicts_reads_a_missing_result_cell_as_not_passed():
    """GFM の表は欠けたセルを空として読む。`結果` の欠けた行は合格でない（前は表ごと読めない扱い）。"""
    block = "## リリース後テスト\n\n| 課題 | 条件 | 結果 |\n| --- | --- | --- |\n| #7 | a | 合格 |\n| #8 | b |\n\n合否: 不合格\n"
    got = load_sprint_close().verification_verdicts(block, "o/r")
    assert {k: v.all_pass for k, v in got.items()} == {("o/r", 7): True, ("o/r", 8): False}


# --- リリース承認ゲート（自動反映の本番チャネルへのマージ。#1336） ------------------------


def declare(repo, wt, rows=None):
    """仮のリポジトリへサンプルと同じ宣言を置く（rows が None なら project.json を置かない）。"""
    nd = repo / ".ndf"
    nd.mkdir(exist_ok=True)
    (nd / "worktree.json").write_text(json.dumps({"version": 1, **wt}) if isinstance(wt, dict) else wt, encoding="utf-8")
    if rows is not None:
        (nd / "project.json").write_text(json.dumps({"delivery": rows}, ensure_ascii=False), encoding="utf-8")


CONSOLE = (
    {"base_branch": "main", "production_branch": "main"},
    [{"target": "本番（ECS）", "kind": "auto", "trigger": "t", "branch": "main", "versioned": False}],
)
TRYGROUP = (
    {"base_branch": "develop", "production_branch": "main"},
    [
        {"target": "stg", "kind": "auto", "trigger": "t", "branch": "develop", "versioned": False},
        {"target": "prd", "kind": "auto", "trigger": "t", "branch": "main", "versioned": False},
    ],
)


def merges(gh):
    return [c for c in gh.get()["calls"] if c[:2] == ["pr", "merge"]]


def to(base, sha="a"):
    return {
        "baseRefName": base,
        "headRefName": "feat/x",
        "url": "https://github.com/o/r/pull/5",
        "additions": 3,
        "deletions": 1,
        "changedFiles": 2,
        **passed_pr(sha),
    }


def test_merge_when_green_stops_production_merge_without_approval(repo, gh):
    """AC1・I1: carmo-system-console の宣言で main 宛ては、CI を待たず gh pr merge も ready も打たずに 10 で終える。"""
    declare(repo, *CONSOLE)
    gh.set(pr_seq={"5": [dict(to("main"), isDraft=True)]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup"], gh.env, repo)
    assert code == 10, (out, err)
    assert out["status"] == "gate" and out["metrics"] == {"gate": "production-merge", "verdict": "production", "target": "main"}
    names = [i["name"] for i in out["items"] if i["kind"] == "decl"]
    assert ".ndf/worktree.json の production_branch" in names and ".ndf/project.json の delivery[0]" in names
    assert "--gate-approved user" in out["next"] and Path(out["presentation_path"]).is_file()
    assert "https://github.com/o/r/pull/5" in Path(out["presentation_path"]).read_text(encoding="utf-8")
    assert merges(gh) == [] and not [c for c in gh.get()["calls"] if c[:2] == ["pr", "ready"]]


def test_merge_when_green_trygroup_stops_main_and_passes_develop(repo, gh):
    """AC2: 本番（main）宛ては止まり、検証（develop）宛てはマージへ進む。"""
    declare(repo, *TRYGROUP)
    gh.set(pr_seq={"5": [to("main")]})
    code, out, _ = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup"], gh.env, repo)
    assert code == 10 and merges(gh) == []
    gh.set(pr_seq={"5": [to("develop"), to("develop"), to("develop")]})
    code, out, err = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--recheck", "0", "--no-cleanup"], gh.env, repo)
    assert code == 0, (out, err)
    assert len(merges(gh)) == 1 and out["metrics"]["verdict"] == "not-production"


def test_merge_when_green_proceeds_with_approval_and_records_it(repo, gh):
    """AC8・I8: 止まった同じ PR は --gate-approved でマージへ進み、担い手と先頭のコミットを結果に残す。無ければ何度でも止まる。"""
    declare(repo, *CONSOLE)
    for _ in range(2):
        gh.set(pr_seq={"5": [to("main")]})
        code, _, _ = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup"], gh.env, repo)
        assert code == 10 and merges(gh) == []
    gh.set(pr_seq={"5": [to("main", "bbb")] * 3})
    code, out, err = call(
        "merged-steps.py",
        ["merge-when-green", "5", "--interval", "0", "--recheck", "0", "--no-cleanup", "--gate-approved", "user"],
        gh.env,
        repo,
    )
    assert code == 0, (out, err)
    merged = next(i for i in out["items"] if i["kind"] == "pr" and i["result"] == "merged")
    assert merged["gate_approved"] == "user" and merged["head"] == "bbb"
    assert merges(gh) == [["pr", "merge", "5", "--admin", "--merge", "--match-head-commit", "bbb"]]


def test_merge_when_green_no_admin_merges_without_the_bypass(repo, gh):
    """#2773 の I7: --no-admin なら gh pr merge に --admin を付けず、マージの記録に admin: false を残す。"""
    declare(repo, *CONSOLE)
    gh.set(pr_seq={"5": [to("main", "bbb")] * 3})
    args = ["merge-when-green", "5", "--interval", "0", "--recheck", "0", "--no-cleanup", "--gate-approved", "user", "--no-admin"]
    code, out, err = call("merged-steps.py", args, gh.env, repo)
    assert code == 0, (out, err)
    assert merges(gh) == [["pr", "merge", "5", "--merge", "--match-head-commit", "bbb"]]
    assert next(i for i in out["items"] if i.get("result") == "merged")["admin"] is False


@pytest.mark.parametrize("broken", ["worktree", "project"])
def test_merge_when_green_stops_on_broken_declaration(repo, gh, broken):
    """AC7: 宣言が壊れていれば、本番系へ出るかを決められないとして止まる（止めない側へ倒さない）。"""
    if broken == "worktree":
        declare(repo, "{", CONSOLE[1])
    else:
        declare(repo, CONSOLE[0])
        (repo / ".ndf" / "project.json").write_text("[1,", encoding="utf-8")
    gh.set(pr_seq={"5": [to("develop")]})
    code, out, _ = call("merged-steps.py", ["merge-when-green", "5", "--interval", "0", "--no-cleanup"], gh.env, repo)
    assert code == 10 and out["metrics"]["verdict"] == "undetermined" and merges(gh) == []
    assert f"{broken}.json" in out["summary"]
    code, out, _ = call("merged-steps.py", ["merge-gate", "--base", "develop"], gh.env, repo)
    assert code == 10 and out["metrics"]["verdict"] == "undetermined"


def test_merge_gate_judges_without_gh(repo, gh):
    """AC6・I3: merge-gate は宛先の引数と宣言だけで判定する（--pr が無ければ gh を呼ばない）。"""
    declare(
        repo,
        {"base_branch": "develop", "production_branch": "main"},
        [{"target": "ndf", "kind": "manual", "trigger": "t", "branch": "main", "versioned": True}],
    )
    code, out, _ = call("merged-steps.py", ["merge-gate", "--base", "develop"], gh.env, repo)
    assert code == 0 and out["metrics"]["verdict"] == "not-production"
    declare(repo, *CONSOLE)
    code, out, _ = call("merged-steps.py", ["merge-gate", "--base", "main"], gh.env, repo)
    assert code == 10 and out["metrics"]["gate"] == "production-merge"
    assert gh.get().get("calls", []) == []


def test_promote_creates_pr_and_merges_after_approval_without_cleanup(repo, gh, tmp_path):
    """I7: リリースの Pull Request を作り、承認が無ければ止め、承認があればマージして後片付けをしない。"""
    declare(repo, *TRYGROUP)
    gh.set(create_number=9, pr_seq={"9": [dict(to("main", X), headRefName="develop")]})
    code, out, _ = call("merged-steps.py", ["promote", "--head", "develop", "--base", "main", "--interval", "0"], gh.env, repo)
    assert code == 10 and "promote-approved" in out["next"] and merges(gh) == []
    gh.set(open_prs=[{"number": 9}], pr_seq={"9": [dict(to("main", X), headRefName="develop")] * 3})
    code, out, err = call(
        "merged-steps.py",
        # CD のある場合（TRYGROUP）は照合する承認したコミットが要る（#2773）
        [*promote_args("--expect-head-from", str(verified_material(tmp_path)), "--gate-approved", "mvv")],
        gh.env,
        repo,
    )
    assert code == 0, (out, err)
    assert [c for c in gh.get()["calls"] if c[:2] == ["pr", "create"]] == []
    assert next(i for i in out["items"] if i.get("result") == "merged")["gate_approved"] == "mvv"
    assert "removed_worktrees" not in out["metrics"]  # 後片付けをしない（develop を消さない）
    assert "develop" in git(repo, "branch", "--list", "develop")


def test_promote_prepare_writes_material_and_stops_short(repo, gh):
    declare(repo, *TRYGROUP)
    gh.set(create_number=9, pr_seq={"9": [dict(to("main", X), headRefName="develop")]})
    code, out, _ = call("merged-steps.py", ["promote", "--head", "develop", "--base", "main", "--prepare"], gh.env, repo)
    assert code == 0 and out["metrics"]["pr"] == 9 and Path(out["presentation_path"]).is_file()
    assert merges(gh) == []
    assert approved_commit.material_sha(out["presentation_path"]) == X  # #2104 の AC6・I2


# --- #1485: 手動確認（マージ前）の行が未確認なら merge-gate が止まる ---------------------------------

MANUAL_BODY = "## 手動確認\n\n- [{box}] #7 マージ前: 実機で承認資料の欄を見る\n- #7 リリース後テストへ回す: 進行を見る\n\n## 次\n"


@pytest.mark.parametrize(
    "body, code",
    [
        (MANUAL_BODY.format(box=" "), 10),  # AC17: 未確認のマージ前の行
        (MANUAL_BODY.format(box="x"), 0),  # AC17: 印を付けたら続く
        ("## 手動確認\n\n- #7 リリース後テストへ回す: 進行を見る\n", 0),  # AC18
        ("## 利用者向けの変化\n\n- 無し\n", 0),  # AC19
    ],
)
def test_merge_gate_stops_on_unchecked_before_merge_rows(repo, gh, body, code):
    declare(repo, *TRYGROUP)
    gh.set(pr_seq={"5": [dict(to("develop"), body=body)]})
    got, out, err = call("merged-steps.py", ["merge-gate", "--pr", "5"], gh.env, repo)
    assert got == code, (out, err)
    assert len(pr_views(gh)) == 1  # 本文と宛先を 1 回の gh pr view で読む
    if code == 10:
        assert out["metrics"]["gate"] == "manual-check" and "実機で承認資料の欄を見る" in out["summary"]
        assert "--from merge-gate" in out["next"]


# --- #1529: fast / auto のリリースの Pull Request は、導入の確認で確かめたコミットのときだけマージする ----------------------

X, Y = "a" * 40, "b" * 40


def verified_material(tmp_path, *lines):
    """導入の確認の承認資料（verify-facts が書く形）。lines を省けば確かめたコミットを X にする。"""
    sys.path.insert(0, str(SCRIPTS))
    from release_lib.deploy import verify_markdown

    p = tmp_path / "approval-verify.md"
    p.write_text("\n".join(lines) if lines else verify_markdown("develop", X, "true", 0), encoding="utf-8")
    return p


def promote_mvv(repo, gh, material):
    args = ["promote", "--head", "develop", "--base", "main", "--interval", "0", "--recheck", "0"]
    return call("merged-steps.py", [*args, "--expect-head-from", str(material), "--gate-approved", "mvv"], gh.env, repo)


def test_promote_merges_the_verified_commit(repo, gh, tmp_path):
    """AC1・I1: 先端が確かめたコミットのまま CI が通れば、そのコミットに固定してマージする。"""
    declare(repo, *TRYGROUP)
    gh.set(open_prs=[{"number": 9}], pr_seq={"9": [dict(to("main", X), headRefName="develop")] * 3})
    code, out, err = promote_mvv(repo, gh, verified_material(tmp_path))
    assert code == 0, (out, err)
    # CD のある場合（TRYGROUP は main へのマージで届く）は証跡を書いてから迂回なしでマージする（#2773 の決定 14）
    assert merges(gh) == [["pr", "merge", "9", "--merge", "--match-head-commit", X]]
    assert next(i for i in out["items"] if i.get("result") == "merged")["head"] == X
    assert X in gh.get()["comments"][0] and "ndf:release-approval" in gh.get()["comments"][0]


@pytest.mark.parametrize(
    "seq",
    [
        [dict(to("main", Y), headRefName="develop")] * 2,  # 確認の後、最初の読みまでに進んだ
        [dict(to("main", X), headRefName="develop"), dict(to("main", Y), headRefName="develop")],  # CI を待つ間に進んだ
        [{"state": "MERGED", "headRefOid": Y, "mergeCommit": {"oid": "c"}, "baseRefName": "main"}],  # 別の先端でマージ済み
    ],
)
def test_promote_stops_when_the_head_moved_from_the_verified_commit(repo, gh, tmp_path, seq):
    """AC2・AC3・I1: 先端が確かめたコミットと違えば gh pr merge を呼ばず、head_moved と両方の SHA を出して 1。"""
    declare(repo, *TRYGROUP)
    gh.set(open_prs=[{"number": 9}], pr_seq={"9": seq})
    code, out, err = promote_mvv(repo, gh, verified_material(tmp_path))
    assert code == 1 and out["status"] == "stopped", (out, err)
    assert {"kind": "pr", "name": "#9", "result": "head_moved", "head": Y, "expected": X} in out["items"]
    assert merges(gh) == []


@pytest.mark.parametrize(
    "lines",
    [
        None,  # 資料が無い
        ("# 導入の確認の承認資料", "", "- コマンド: `true`"),  # 行が無い
        (f"- ベースブランチ develop の先頭: `{X}`。導入の確認はこのコミットで走らせた",) * 2,  # 行が 2 つ
        (f"- ベースブランチ develop の先頭: `{X[:39]}`。導入の確認はこのコミットで走らせた",),  # 39 桁
    ],
)
def test_promote_does_not_merge_without_a_readable_verified_commit(repo, gh, tmp_path, lines):
    """AC5・I2: 確かめたコミットを読めなければ、gh を 1 度も呼ばずに 2 で止まる（照合を飛ばさない）。"""
    declare(repo, *TRYGROUP)
    material = tmp_path / "missing.md" if lines is None else verified_material(tmp_path, *lines)
    code, out, err = promote_mvv(repo, gh, material)
    assert code == 2 and out["status"] == "stopped", (out, err)
    assert gh.get().get("calls", []) == []


def test_promote_help_shows_the_verified_commit_argument(gh, repo):
    """AC9・#2142: promote の --help に照合の引数と、食い違えばリリース承認ゲートへ戻ることが出る。"""
    p = subprocess.run([PY, str(SCRIPTS / "merged-steps.py"), "promote", "--help"], capture_output=True, text=True, env=gh.env)
    text = "".join(p.stdout.split())  # argparse の折り返しを外す
    assert p.returncode == 0 and "--expect-head-from" in text
    assert "リリース承認ゲートへ戻す" in text and "--fromverify" in text


# --- #2104: リリースの承認の後のマージは、リリース承認ゲートの承認資料の承認したコミットのときだけ行う ----------------------


def promote_args(*extra):
    return ["promote", "--head", "develop", "--base", "main", "--interval", "0", "--recheck", "0", *extra]


def promote_material(tmp_path, sha=X, row="承認したコミット"):
    """リリースの承認資料（gate_stop が書く形の判断の表の行）。sha が None なら資料を作らない。"""
    p = tmp_path / "approval-promote.md"
    if sha is not None:
        p.write_text(f"## 2. 承認の判断に使うもの\n\n| 項目 | 内容 |\n| --- | --- |\n| {row} | {sha} |\n", encoding="utf-8")
    return p


def test_promote_gate_writes_the_head_into_the_material(repo, gh, tmp_path):
    """AC1・AC8・I1: リリース承認ゲートで止まるとき、最初の読みの先端を資料・metrics・gate の items に載せ、next が資料で照合する。"""
    declare(repo, *TRYGROUP)
    out_path = tmp_path / "approval-promote.md"
    gh.set(create_number=9, pr_seq={"9": [dict(to("main", X), headRefName="develop"), dict(to("main", Y), headRefName="develop")]})
    code, out, err = call("merged-steps.py", promote_args("--out", str(out_path)), gh.env, repo)
    assert code == 10, (out, err)
    assert approved_commit.material_sha(out_path) == X and out["metrics"]["approved_sha"] == X
    assert next(i for i in out["items"] if i.get("result") == "gate")["head"] == X
    assert f"--approved-from {out_path}" in out["next"] and merges(gh) == []


def test_promote_gate_does_not_write_a_material_for_an_unreadable_head(repo, gh, tmp_path):
    """I1: 先端が 40 桁の 16 進で読めなければ、資料を書かずに 2 で終える。"""
    declare(repo, *TRYGROUP)
    out_path = tmp_path / "approval-promote.md"
    gh.set(create_number=9, pr_seq={"9": [dict(to("main", "a"), headRefName="develop")]})
    code, out, err = call("merged-steps.py", promote_args("--out", str(out_path)), gh.env, repo)
    assert code == 2 and out["status"] == "stopped", (out, err)
    assert not out_path.exists() and merges(gh) == []


def test_promote_approved_merges_the_approved_commit(repo, gh, tmp_path):
    """AC2・I3: 資料が X・先端が X のまま CI が通れば、X に固定して 1 度だけマージする。"""
    declare(repo, *TRYGROUP)
    gh.set(open_prs=[{"number": 9}], pr_seq={"9": [dict(to("main", X), headRefName="develop")] * 3})
    material = promote_material(tmp_path)
    code, out, err = call("merged-steps.py", promote_args("--approved-from", str(material), "--gate-approved", "user"), gh.env, repo)
    assert code == 0, (out, err)
    assert merges(gh) == [["pr", "merge", "9", "--merge", "--match-head-commit", X]]  # CD のある場合は迂回なし（#2773）


@pytest.mark.parametrize(
    "seq",
    [
        [dict(to("main", Y), headRefName="develop")] * 2,  # 承認の前（か後）に進んだ
        [dict(to("main", X), headRefName="develop"), dict(to("main", Y), headRefName="develop")],  # CI を待つ間に進んだ
    ],
)
def test_promote_approved_stops_when_the_head_moved(repo, gh, tmp_path, seq):
    """AC3・I3: 先端が承認したコミットと違えば、マージせずに head_moved で 1。next がリリース承認ゲートからのやり直しを示す。"""
    declare(repo, *TRYGROUP)
    gh.set(open_prs=[{"number": 9}], pr_seq={"9": seq})
    material = promote_material(tmp_path)
    code, out, err = call("merged-steps.py", promote_args("--approved-from", str(material), "--gate-approved", "user"), gh.env, repo)
    assert code == 1 and out["status"] == "stopped", (out, err)
    assert {"kind": "pr", "name": "#9", "result": "head_moved", "head": Y, "expected": X} in out["items"]
    assert "--from promote" in out["next"] and merges(gh) == []


@pytest.mark.parametrize(
    "sha, row",
    [(None, "承認したコミット"), (X, "確かめたコミット"), (X[:39], "承認したコミット"), ("z" * 40, "承認したコミット")],
)
def test_promote_approved_does_not_merge_without_a_readable_approved_commit(repo, gh, tmp_path, sha, row):
    """AC4・I4: 資料が無い・欄が無い・40 桁の 16 進でなければ、gh を 1 度も呼ばずに 2 で止まる。"""
    declare(repo, *TRYGROUP)
    material = promote_material(tmp_path, sha, row)
    code, out, err = call("merged-steps.py", promote_args("--approved-from", str(material), "--gate-approved", "user"), gh.env, repo)
    assert code == 2 and out["status"] == "stopped", (out, err)
    assert gh.get().get("calls", []) == []


def test_promote_user_approval_needs_the_material(repo, gh):
    """I5: --gate-approved user を --approved-from 無しで受けず、gh を 1 度も呼ばずに 2 で止まり、資料つきの打ち方を示す。"""
    declare(repo, *TRYGROUP)
    code, out, err = call("merged-steps.py", promote_args("--gate-approved", "user"), gh.env, repo)
    assert code == 2 and out["status"] == "stopped", (out, err)
    assert "--approved-from" in out["next"] and gh.get().get("calls", []) == []


# 現状固定テスト（I-010: merged_lib/merge.py の _trail_before_merge を promote から通す。期待値は今の実装の出力）
REVIEW_RULES = [{"type": "pull_request", "parameters": {"required_approving_review_count": 1}}]
REDO = "リリース承認ゲートからやり直す: プランなら run <プラン> --from promote（fast / auto は --from verify）"


def _promote_cd(repo, gh, tmp_path, *args, material=True, **state):
    declare(repo, *TRYGROUP)
    gh.set(**{"open_prs": [{"number": 9}], "pr_seq": {"9": [dict(to("main", X), headRefName="develop")] * 3}, **state})
    extra = ["--approved-from", str(promote_material(tmp_path))] if material else []
    return call("merged-steps.py", promote_args(*extra, *(args or ("--gate-approved", "user"))), gh.env, repo)


def _approved_review(login, commit):
    return {"user": {"login": login}, "state": "APPROVED", "commit_id": commit, "submitted_at": "2026-11-13T09:00:00Z"}


def test_promote_review_case_merges_when_valid_review_matches(repo, gh, tmp_path):
    code, out, err = _promote_cd(repo, gh, tmp_path, rules=REVIEW_RULES, reviews=[_approved_review("u", X)], perms={"u": "write"})
    assert code == 0 and out["status"] == "ok", (out, err)
    assert {"kind": "review", "name": "#9", "result": "approved", "approved_sha": X} in out["items"]
    assert out["items"][-1] == {
        "kind": "pr",
        "name": "#9",
        "result": "merged",
        "method": "merge",
        "head": X,
        "admin": False,
        "gate_approved": "user",
    }
    assert merges(gh) == [["pr", "merge", "9", "--merge", "--match-head-commit", X]] and not gh.get().get("comments")


@pytest.mark.parametrize(
    "reviews, perms, summary, review_commit",
    [
        ([], {}, "#9 をマージしない: 有効な人の承認のレビューが無い", None),
        ([_approved_review("u", X)], {"u": "read"}, "#9 をマージしない: 有効な人の承認のレビューが無い", None),
        ([_approved_review("u", Y)], {"u": "admin"}, "#9 をマージしない: 承認のレビュー bbbbbbbb が承認したコミット aaaaaaaa と違う", Y),
    ],
)
def test_promote_review_case_gates_without_matching_review(repo, gh, tmp_path, reviews, perms, summary, review_commit):
    code, out, err = _promote_cd(repo, gh, tmp_path, rules=REVIEW_RULES, reviews=reviews, perms=perms)
    assert code == 10 and out["status"] == "gate", (out, err)
    assert out["summary"] == summary and out["items"] == [] and out["next"] == REDO
    assert out["metrics"] == {"approved_sha": X, "review_commit": review_commit}
    assert merges(gh) == []


def test_promote_review_case_stops_when_reviews_are_unreadable(repo, gh, tmp_path):
    code, out, err = _promote_cd(repo, gh, tmp_path, rules=REVIEW_RULES)
    assert code == 1 and out["status"] == "stopped", (out, err)
    assert out["summary"].startswith("PR #9 のレビューを読めない") and merges(gh) == []


def test_promote_no_review_case_writes_trail_then_merges(repo, gh, tmp_path):
    code, out, err = _promote_cd(repo, gh, tmp_path)
    assert code == 0, (out, err)
    assert {"kind": "comment", "name": "https://github.com/o/r/pull/9#c1", "result": "written", "approved_sha": X} in out["items"]
    assert len(gh.get()["comments"]) == 1 and merges(gh) == [["pr", "merge", "9", "--merge", "--match-head-commit", X]]


def test_promote_no_review_case_waits_for_trail_status_before_merge(repo, gh, tmp_path):
    """証跡のチェックが必須なら、証跡を書いた後にその status が success になるのを確かめてからマージへ入る。"""
    rules = [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "ndf/release-approval"}]}}]
    code, out, err = _promote_cd(repo, gh, tmp_path, rules=rules, statuses=[{"context": "ndf/release-approval", "state": "success"}])
    assert code == 0, (out, err)
    calls = gh.get()["calls"]
    status = next(i for i, c in enumerate(calls) if c[:1] == ["api"] and c[-1].endswith(f"/commits/{X}/status"))
    assert status < next(i for i, c in enumerate(calls) if c[:2] == ["pr", "merge"])


def test_promote_no_review_case_keeps_own_existing_trail(repo, gh, tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    from release_lib import approval_trail

    mine = {"id": 5, "body": approval_trail.render_trail(X, "user"), "user": {"login": "me"}, "created_at": "t", "updated_at": "t"}
    mine["html_url"] = "https://github.com/o/r/pull/9#c5"
    code, out, err = _promote_cd(repo, gh, tmp_path, issue_comments=[mine])
    assert code == 0, (out, err)
    assert {"kind": "comment", "name": "https://github.com/o/r/pull/9#c5", "result": "kept", "approved_sha": X} in out["items"]
    assert not gh.get().get("comments") and len(merges(gh)) == 1


def test_promote_no_review_case_does_not_write_trail_when_head_moved(repo, gh, tmp_path):
    code, out, err = _promote_cd(repo, gh, tmp_path, pr_seq={"9": [dict(to("main", Y), headRefName="develop")] * 3})
    assert code == 1 and out["status"] == "stopped", (out, err)
    assert out["items"] == [{"kind": "pr", "name": "#9", "result": "head_moved", "head": Y, "expected": X}]
    assert not gh.get().get("comments") and merges(gh) == []


def test_promote_cd_case_needs_the_approved_commit(repo, gh, tmp_path):
    code, out, err = _promote_cd(repo, gh, tmp_path, "--gate-approved", "mvv", material=False)
    assert code == 3 and out["status"] == "stopped", (out, err)
    assert (
        out["summary"] == "CD のある場合の promote には照合する承認したコミット（--approved-from か --expect-head-from）とリポジトリが要る"
    )
    assert merges(gh) == []


# 現状固定テスト（I-003: release_lib/approval.py の approved_on_pr。期待値は今の実装の出力）
def _approval_on_pr(monkeypatch, comments=(), reviews=(), roles=None, perms=None):
    """approval_trail の GitHub の読みを決まった値に置き換え、読んだ引数を seen に積む。"""
    sys.path.insert(0, str(SCRIPTS))
    from release_lib import approval

    seen: dict = {}
    t = approval.trail
    monkeypatch.setattr(t, "read_comments", lambda repo, pr: seen.setdefault("comments", (repo, pr)) and list(comments))
    monkeypatch.setattr(t, "read_reviews", lambda repo, pr: seen.setdefault("reviews", (repo, pr)) and list(reviews))
    monkeypatch.setattr(t, "read_roles", lambda repo, logins: seen.setdefault("roles", list(logins)) is not None and dict(roles or {}))
    monkeypatch.setattr(
        t, "read_permissions", lambda repo, logins: seen.setdefault("perms", list(logins)) is not None and dict(perms or {})
    )
    return approval, seen


def _trail_comment(sha, login, created, updated=None, cid=1):
    from release_lib import approval_trail

    body = approval_trail.render_trail(sha, "user")
    return {"id": cid, "body": body, "user": {"login": login}, "created_at": created, "updated_at": updated or created}


def _review(login, state, commit, at="2026-11-13T09:00:00Z"):
    return {"user": {"login": login}, "state": state, "commit_id": commit, "submitted_at": at}


T0, T1 = "2026-11-13T09:00:00Z", "2026-11-13T10:00:00Z"
NO_REVIEW, REVIEW, NO_CD = (True, 0, ""), (True, 1, ""), (False, None, "")


@pytest.mark.parametrize(
    "case, comments, roles, head, expected, read_roles",
    [
        (NO_REVIEW, [], {}, X, ("failure", "証跡が無い", None, None), []),
        (NO_REVIEW, [(X, "adm", T0)], {"adm": "admin"}, X, ("success", f"承認: adm {T0}", "adm", T0), ["adm"]),
        (NO_REVIEW, [(Y, "adm", T0)], {"adm": "admin"}, X, ("failure", "証跡 bbbbbbb ≠ 先端 aaaaaaa", "adm", T0), ["adm"]),
        (NO_REVIEW, [(X, "adm", T0)], {"adm": "admin"}, X.upper(), ("success", f"承認: adm {T0}", "adm", T0), ["adm"]),
        (NO_CD, [(X, "m", T0)], {"m": "maintain"}, X, ("success", f"承認: m {T0}", "m", T0), ["m"]),
        (REVIEW, [(X, "adm", T0)], {"adm": "admin"}, X, ("failure", "有効な承認のレビューが無い", None, None), None),
    ],
)
def test_approved_on_pr_reads_trails_except_in_review_case(monkeypatch, case, comments, roles, head, expected, read_roles):
    """cd-review 以外（no-cd も）は証跡を読む。cd-review はコメントを読まずレビューだけを見る。"""
    import release_case

    approval, seen = _approval_on_pr(monkeypatch, [_trail_comment(*c) for c in comments], roles=roles)
    got = approval.approved_on_pr("o/r", 9, head, release_case.decide_case(*case))
    assert tuple(got) == expected
    if read_roles is None:
        assert "comments" not in seen and "roles" not in seen and seen["reviews"] == ("o/r", 9)
    else:
        assert seen["comments"] == ("o/r", 9) and seen["roles"] == read_roles and "reviews" not in seen


def test_approved_on_pr_does_not_read_roles_of_edited_trails(monkeypatch):
    import release_case

    comments = [_trail_comment(X, "adm", T0, T1), _trail_comment(X, "w", "2026-11-13T09:30:00Z", cid=2)]
    approval, seen = _approval_on_pr(monkeypatch, comments, roles={"w": "write"})
    got = approval.approved_on_pr("o/r", 9, X, release_case.decide_case(*NO_REVIEW))
    assert tuple(got) == ("failure", "承認権限の無い w の証跡だけがある", None, None)
    assert seen["roles"] == ["w"]


@pytest.mark.parametrize(
    "reviews, perms, head, expected, asked",
    [
        ([], {}, X, ("failure", "有効な承認のレビューが無い", None, None), []),
        (
            [_review("u", "APPROVED", X), _review("v", "COMMENTED", Y), _review("x", "APPROVED", Y, T1)],
            {"u": "write", "x": "read"},
            X,
            ("success", "承認のレビュー aaaaaaa", None, None),
            ["u", "x"],
        ),
        ([_review("u", "APPROVED", X.upper())], {"u": "admin"}, X.upper(), ("success", "承認のレビュー aaaaaaa", None, None), ["u"]),
        (
            [_review("u", "APPROVED", X), _review("x", "APPROVED", Y, T1)],
            {"u": "write", "x": "admin"},
            X,
            ("failure", "承認のレビュー bbbbbbb ≠ 先端 aaaaaaa", None, None),
            ["u", "x"],
        ),
        ([_review("u", "APPROVED", X)], {"u": "read"}, X, ("failure", "有効な承認のレビューが無い", None, None), ["u"]),
    ],
)
def test_approved_on_pr_review_case_matches_latest_valid_approval(monkeypatch, reviews, perms, head, expected, asked):
    """cd-review は APPROVED の人だけの権限を読み、有効な人の最新の承認のコミットと先端を照らす。"""
    import release_case

    approval, seen = _approval_on_pr(monkeypatch, reviews=reviews, perms=perms)
    got = approval.approved_on_pr("o/r", 9, head, release_case.decide_case(*REVIEW))
    assert tuple(got) == expected
    assert seen["perms"] == asked
