"""upkeep.py（candidates / apply / report）。

gh は PATH の先頭に置いた偽物で置き換える。偽物は FAKE_GH_STATE の JSON を課題の置き場として
読み書きし、呼ばれた引数を calls に積む。`throttle` に積んだ応答は、書き込みの呼び出しへ
先頭から 1 つずつ返す（上限に当たった応答を作るため）。`fail` に書いたパスの一部を含む書き込みは
上限ではない失敗で返す。ラベルのパスは GitHub と同じく 1 つの segment として読み、`/` を含めば 404 にする。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
PLUGIN_ROOT = SKILL.parents[1]
SCRIPT = SKILL / "scripts" / "upkeep.py"
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
from step_result import validate_result  # noqa: E402

PY = sys.executable
FUTURE = "2999-01-01T00:00:00Z"

FAKE_GH = r"""#!{py}
import json, os, re, sys, urllib.parse
a = sys.argv[1:]
path = os.environ["FAKE_GH_STATE"]
st = json.load(open(path, encoding="utf-8"))
st.setdefault("calls", []).append(a)
stdin = sys.stdin.read() if "--input" in a else ""
if stdin:
    st.setdefault("inputs", []).append([a, json.loads(stdin)])

def done(body, code=0, headers=None, stderr=""):
    json.dump(st, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    if "-i" in a:
        head = "HTTP/2.0 %s\n" % ("200 OK" if code == 0 else "403 Forbidden")
        head += "".join("%s: %s\n" % kv for kv in (headers or {{}}).items())
        text = head + "\n" + text
    sys.stdout.write(text)
    sys.stderr.write(stderr)
    sys.exit(code)

if a[:2] == ["repo", "view"]:
    done("o/r\n")
if a[0] != "api":
    done("", 1, stderr="unknown")
rest = [x for x in a[1:] if x not in ("-i", "--paginate")]
target = rest[0]
method = rest[rest.index("-X") + 1] if "-X" in rest else "GET"
if target == "rate_limit":
    done("5000\n")
if method != "GET" and st.get("throttle"):
    t = st["throttle"].pop(0)
    done(t.get("body", {{"message": "You have exceeded a secondary rate limit"}}), 1,
         t.get("headers"), "gh: You have exceeded a secondary rate limit (HTTP 403)\n")
if method != "GET" and st.get("fail") and st["fail"] in target:
    done("", 1, stderr="server error (HTTP 500)")
issues = st["issues"]
m = re.match(r"repos/o/r/issues\?state=(\w+)", target)
if m:
    done([i for i in issues.values() if i["state"] == m.group(1)])
if target.startswith("repos/o/r/milestones"):
    if method == "POST":
        ms = {{"number": 100 + len(st["milestones"]), "title": json.loads(stdin)["title"]}}
        st["milestones"].append(ms)
        done(ms)
    done(st["milestones"])
m = re.match(r"repos/o/r/issues/(\d+)/sub_issues", target)
if m:
    done([issues[str(k)] for k in st.get("sub_issues", {{}}).get(m.group(1), [])])
m = re.match(r"repos/o/r/issues/(\d+)/labels(?:/([^/]+))?$", target)
if m:
    i = issues[m.group(1)]
    if method == "POST":
        i["labels"] += [{{"name": x}} for x in json.loads(stdin)["labels"]]
    else:
        name = urllib.parse.unquote(m.group(2))
        i["labels"] = [x for x in i["labels"] if x["name"] != name]
    i["updated_at"] = "2026-09-25T00:00:10Z"
    done(i["labels"])
m = re.match(r"repos/o/r/issues/(\d+)$", target)
if m:
    i = issues[m.group(1)]
    if method == "PATCH":
        patch = json.loads(stdin)
        if "milestone" in patch:
            want = patch.pop("milestone")
            ms = [x for x in st["milestones"] if x["number"] == want]
            i["milestone"] = ms[0] if ms else None
        i.update(patch)
        i["updated_at"] = "2026-09-25T00:00:09Z"
    done(i)
done("", 1, stderr="not found (HTTP 404)")
"""


def _issue(n, title, body="", state="open", milestone=None, **kw):
    return {
        "number": n,
        "title": title,
        "body": body,
        "state": state,
        "milestone": {"number": 1, "title": milestone} if milestone else None,
        "labels": [],
        "updated_at": "2026-09-01T00:00:00Z",
        **kw,
    }


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def env(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "gone-tool.sh").write_text("echo\n")
    (repo / "keep.py").write_text("def old_helper_name():\n    pass\n")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    _git(repo, "tag", "v1")
    (repo / "scripts" / "gone-tool.sh").unlink()
    (repo / "keep.py").write_text("def new_helper_name():\n    pass\n")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "change")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH.format(py=PY))
    gh.chmod(0o755)
    issues = [
        _issue(1, "消えたスクリプト", "scripts/gone-tool.sh が落ちる", milestone="M2"),
        _issue(2, "改名", "`old_helper_name` を直す", milestone="M2"),
        _issue(3, "未設定"),
        _issue(4, "同じ時期", milestone="M1"),
        _issue(5, "子", milestone="M2"),
        _issue(6, "本文で子", "親: #11", milestone="M2"),
        _issue(7, "関係なし", milestone="M2"),
        _issue(10, "閉じた", state="closed", milestone="M1", closed_at=FUTURE),
        _issue(11, "閉じた親", state="closed", closed_at=FUTURE, sub_issues_summary={"total": 1}),
    ]
    state = {
        "issues": {str(i["number"]): i for i in issues},
        "milestones": [{"number": 1, "title": "M1"}, {"number": 2, "title": "M2"}],
        "sub_issues": {"11": [5]},
    }
    sp = tmp_path / "gh-state.json"
    sp.write_text(json.dumps(state, ensure_ascii=False))
    e = dict(
        os.environ,
        PATH=f"{bindir}:{os.environ['PATH']}",
        FAKE_GH_STATE=str(sp),
        NDF_UPKEEP_STATE_DIR=str(tmp_path / "state"),
        NDF_UPKEEP_NO_SLEEP="1",
        NDF_PRESENTATION_DIR=str(tmp_path / "pres"),
    )

    class Env:
        root, path = repo, tmp_path

        def run(self, *args):
            p = subprocess.run([PY, str(SCRIPT), *args, "--root", str(repo)], env=e, capture_output=True, text=True)
            out = json.loads(p.stdout.strip().splitlines()[-1])
            assert validate_result(out, p.returncode) == [], (out, p.returncode, p.stderr)
            return p.returncode, out

        def state(self):
            return json.loads(sp.read_text())

        def set(self, fn):
            st = self.state()
            fn(st)
            sp.write_text(json.dumps(st, ensure_ascii=False))

        def plan(self, actions):
            f = tmp_path / "plan.json"
            f.write_text(json.dumps({"repo": "o/r", "actions": actions}, ensure_ascii=False))
            return str(f)

        def snapshot(self, n):
            _, out = self.run("candidates", "--since-ref", "v1", "--all")
            return next(i for i in out["items"] if i.get("number") == n)

        def writes(self):
            return [c for c in self.state()["calls"] if "-X" in c]

    return Env()


def _action(snap, verdict, changes, **kw):
    return {
        "number": snap["number"],
        "verdict": verdict,
        "updated_at": snap["updated_at"],
        "digest": snap["digest"],
        "changes": changes,
        **kw,
    }


def test_candidates_collect_each_route(env):
    code, out = env.run("candidates", "--since-ref", "v1")
    assert code == 0
    routes = {i["number"]: i["routes"] for i in out["items"] if i["kind"] == "issue"}
    assert routes == {
        1: ["diff-path"],
        2: ["diff-identifier"],
        3: ["no-milestone"],
        4: ["closed-milestone"],
        5: ["sub-issue"],
        6: ["sub-issue"],
    }
    assert out["metrics"]["candidates"] == 6
    assert all(i["kind"] == "issue" for i in out["items"])  # M1 には open の #4 が残る


def test_candidates_report_milestone_without_open_issue(env):
    env.set(lambda st: st["issues"]["4"].update(state="closed", closed_at=FUTURE))
    _, out = env.run("candidates", "--since-ref", "v1")
    assert [i["name"] for i in out["items"] if i["kind"] == "milestone"] == ["M1"]


def test_candidates_without_milestones_skip_those_routes(env):
    env.set(lambda st: st.update(milestones=[]))
    _, out = env.run("candidates", "--since-ref", "v1")
    assert out["metrics"]["by_route"]["no-milestone"] == 0
    assert out["metrics"]["by_route"]["closed-milestone"] == 0 and out["metrics"]["notes"]


def test_candidates_all_and_add(env):
    _, out = env.run("candidates", "--since-ref", "v1", "--all", "--add", "7")
    by = {i["number"]: i["routes"] for i in out["items"] if i["kind"] == "issue"}
    assert set(by) == {1, 2, 3, 4, 5, 6, 7}
    assert by[7] == ["all", "manual"]


def test_candidates_limit_caps_one_run(env):
    code, out = env.run("candidates", "--since-ref", "v1", "--limit", "2")
    assert code == 20 and out["status"] == "gate"
    kept = [i["number"] for i in out["items"] if i.get("result") == "candidate"]
    assert len(kept) == 2 and out["metrics"]["candidates"] == 2
    assert len(out["metrics"]["deferred"]) == 4
    # 上限を超えた候補は items に載せない。載せると区分を決める対象として求められる。
    issues = [i["number"] for i in out["items"] if i["kind"] == "issue"]
    assert issues == kept
    assert not set(issues) & set(out["metrics"]["deferred"])


def _more_open_issues(st, count):
    for n in range(20, 20 + count):
        st["issues"][str(n)] = _issue(n, f"溜まった課題 {n}", milestone="M2")


def test_candidates_without_limit_cap_at_default(env):
    # --limit を省いても上限は効く（#842）。--all で 12 件を集め、既定の 10 件で切る
    env.set(lambda st: _more_open_issues(st, 5))
    code, out = env.run("candidates", "--since-ref", "v1", "--all")
    assert code == 20 and out["metrics"]["limit"] == 10
    assert out["metrics"]["candidates"] == 10 and len(out["metrics"]["deferred"]) == 2
    # 棚卸しの報告に持ち越しの番号と採った上限が載る
    code, rep = env.run("report")
    assert rep["metrics"]["deferred"] == out["metrics"]["deferred"] and rep["metrics"]["limit"] == 10


def test_candidates_limit_comes_from_backlog_decl(env):
    (env.root / ".ndf").mkdir()
    (env.root / ".ndf" / "backlog.json").write_text(json.dumps({"candidate_limit": 3}))
    code, out = env.run("candidates", "--since-ref", "v1")
    assert code == 20 and out["metrics"]["limit"] == 3 and out["metrics"]["candidates"] == 3
    # 引数は宣言に勝つ
    code, out = env.run("candidates", "--since-ref", "v1", "--limit", "6")
    assert code == 0 and out["metrics"]["limit"] == 6 and out["metrics"]["deferred"] == []


@pytest.mark.parametrize("decl,args", [(None, ("--limit", "0")), ({"candidate_limit": 0}, ()), ({"candidate_limit": "10"}, ())])
def test_candidates_reject_invalid_limit_before_collecting(env, decl, args):
    if decl is not None:
        (env.root / ".ndf").mkdir()
        (env.root / ".ndf" / "backlog.json").write_text(json.dumps(decl))
    code, out = env.run("candidates", "--since-ref", "v1", *args)
    assert code == 2 and out["status"] == "stopped"
    assert not env.state().get("calls")  # 候補を集めていない


def test_candidates_zero_is_ok_under_limit(env):
    # 候補が 0 件なら上限を当てても持ち越しにならず、そのまま飛ばせる
    def drop(st):
        for k in ("1", "2", "3", "4", "5", "6"):
            st["issues"].pop(k)
        st["sub_issues"] = {}

    env.set(drop)
    code, out = env.run("candidates", "--since-ref", "v1")
    assert code == 0 and out["status"] == "ok" and out["metrics"]["candidates"] == 0


def test_apply_refuses_plan_for_another_repo(env):
    # plan の repo が記録のリポジトリ（gh repo view の o/r）と違えば、1 件も書かずに止まる（#842 の決定 7）
    snap = env.snapshot(7)
    f = env.path / "plan-other.json"
    f.write_text(json.dumps({"repo": "x/other", "actions": [_action(snap, "追記が要る", {"title": "書き換え"})]}, ensure_ascii=False))
    code, out = env.run("apply", "--plan", str(f))
    assert code == 3 and out["status"] == "stopped"
    assert "x/other" in out["items"][0]["reason"] and "o/r" in out["items"][0]["reason"]
    assert env.writes() == []


def test_apply_writes_only_what_differs(env):
    snap = env.snapshot(3)
    code, out = env.run(
        "apply", "--plan", env.plan([_action(snap, "追記が要る", {"body": "新しい本文", "milestone": "M3", "add_labels": ["bug"]})])
    )
    assert code == 0, out
    assert out["metrics"]["applied"] == [3]
    issue = env.state()["issues"]["3"]
    assert issue["body"] == "新しい本文" and issue["milestone"]["title"] == "M3"
    assert [x["name"] for x in issue["labels"]] == ["bug"]


def test_apply_skips_when_issue_changed_after_snapshot(env):
    snap = env.snapshot(3)
    env.set(lambda st: st["issues"]["3"].update(body="誰かが書き換えた", updated_at="2026-09-20T00:00:00Z"))
    code, out = env.run("apply", "--plan", env.plan([_action(snap, "追記が要る", {"body": "新"})]))
    assert code == 20 and out["metrics"]["skipped_changed"] == [3]
    assert env.state()["issues"]["3"]["body"] == "誰かが書き換えた"
    assert not env.writes()


def test_apply_proceeds_when_only_updated_at_moved(env):
    snap = env.snapshot(3)
    env.set(lambda st: st["issues"]["3"].update(updated_at="2026-09-20T00:00:00Z"))  # コメントだけが付いた
    code, out = env.run("apply", "--plan", env.plan([_action(snap, "追記が要る", {"body": "新"})]))
    assert code == 0 and out["metrics"]["applied"] == [3]


@pytest.mark.parametrize(
    "edit",
    [
        {"labels": [{"name": "x"}]},
        {"title": "誰かが題名を直した"},
        {"milestone": {"number": 2, "title": "M2"}},
    ],
)
def test_apply_skips_when_other_fields_changed(env, edit):
    snap = env.snapshot(3)
    env.set(lambda st: st["issues"]["3"].update(updated_at="2026-09-20T00:00:00Z", **edit))
    code, out = env.run("apply", "--plan", env.plan([_action(snap, "追記が要る", {"body": "新", "milestone": "M1"})]))
    assert code == 20 and out["metrics"]["skipped_changed"] == [3]
    assert not env.writes()


def test_apply_removes_label_with_slash(env):
    env.set(lambda st: st["issues"]["3"].update(labels=[{"name": "status/blocked"}, {"name": "bug"}]))
    snap = env.snapshot(3)
    code, out = env.run("apply", "--plan", env.plan([_action(snap, "追記が要る", {"remove_labels": ["status/blocked"]})]))
    assert code == 0, out
    assert [x["name"] for x in env.state()["issues"]["3"]["labels"]] == ["bug"]


def test_apply_same_change_in_a_later_run_is_not_already(env):
    snap = env.snapshot(4)
    ch = {"state": "closed", "state_reason": "completed"}
    env.run("apply", "--plan", env.plan([_action(snap, "閉じてよい", ch)]))
    env.set(lambda st: st["issues"]["4"].update(state="open", updated_at="2026-09-26T00:00:00Z"))  # 再び開かれた
    snap = env.snapshot(4)
    code, out = env.run("apply", "--plan", env.plan([_action(snap, "閉じてよい", ch)]))
    assert code == 0 and out["metrics"]["applied"] == [4]
    assert env.state()["issues"]["4"]["state"] == "closed"


def test_apply_rerun_after_partial_write_finishes_the_rest(env):
    snap = env.snapshot(3)
    plan = env.plan([_action(snap, "追記が要る", {"body": "新", "add_labels": ["bug"]})])
    env.set(lambda st: st.update(fail="/labels"))
    code, out = env.run("apply", "--plan", plan)
    assert code == 1 and out["metrics"]["failed"] == [3]
    assert env.state()["issues"]["3"]["body"] == "新"
    env.set(lambda st: st.update(fail=None))
    code, out = env.run("apply", "--plan", plan)
    assert code == 0 and out["metrics"]["applied"] == [3], out
    assert [x["name"] for x in env.state()["issues"]["3"]["labels"]] == ["bug"]


def test_apply_twice_does_not_write_twice(env):
    snap = env.snapshot(4)
    plan = env.plan([_action(snap, "閉じてよい", {"state": "closed", "state_reason": "completed"})])
    env.run("apply", "--plan", plan)
    first = len(env.writes())
    code, out = env.run("apply", "--plan", plan)
    assert code == 0 and out["metrics"]["already"] == [4]
    assert len(env.writes()) == first == 1


def test_apply_no_write_when_nothing_changes(env):
    snap = env.snapshot(4)
    code, out = env.run("apply", "--plan", env.plan([_action(snap, "そのまま", {"title": "同じ時期"})]))
    assert code == 0 and out["metrics"]["unchanged"] == [4]
    assert not env.writes()


def test_no_work_needs_approval_then_applies(env):
    snap = env.snapshot(7)
    ch = {"state": "closed", "state_reason": "not_planned", "add_labels": ["wontfix"]}
    code, out = env.run("apply", "--plan", env.plan([_action(snap, "やらない", ch)]))
    assert code == 10 and out["metrics"]["needs_approval"] == [7]
    assert Path(out["presentation_path"]).is_file() and not env.writes()
    code, out = env.run("apply", "--plan", env.plan([_action(snap, "やらない", ch, approved=True)]))
    assert code == 0 and env.state()["issues"]["7"]["state"] == "closed"


@pytest.mark.parametrize("approved", ["false", 1])
def test_no_work_rejects_non_bool_approved(env, approved):
    snap = env.snapshot(7)
    ch = {"state": "closed", "state_reason": "not_planned"}
    code, _ = env.run("apply", "--plan", env.plan([_action(snap, "やらない", ch, approved=approved)]))
    assert code == 2 and not env.writes()


def test_needs_judgement_is_returned(env):
    snap = env.snapshot(7)
    code, out = env.run("apply", "--plan", env.plan([_action(snap, "要判断", {"body": "x"})]))
    assert code == 0 and out["metrics"]["returned"] == [7] and not env.writes()


def test_waits_follow_retry_after_then_double(env):
    env.set(lambda st: st.update(throttle=[{"headers": {"Retry-After": "7"}}, {}, {}]))
    snap = env.snapshot(3)
    code, out = env.run("apply", "--plan", env.plan([_action(snap, "追記が要る", {"body": "新"})]))
    assert code == 0, out
    assert [(w["why"], w["seconds"]) for w in out["metrics"]["waits"]] == [("retry-after", 7.0), ("doubling", 60.0), ("doubling", 120.0)]


def test_waits_over_limit_stop_as_partial(env):
    env.set(lambda st: st.update(throttle=[{}] * 5))
    s3, s4 = env.snapshot(3), env.snapshot(4)
    code, out = env.run(
        "apply",
        "--max-waits",
        "2",
        "--plan",
        env.plan([_action(s3, "追記が要る", {"body": "新"}), _action(s4, "追記が要る", {"body": "新"})]),
    )
    assert code == 20 and out["metrics"]["partial"] is True
    assert out["metrics"]["pending"] == [3, 4] and len(out["metrics"]["waits"]) == 2


def test_report_summarises_candidates_and_apply(env):
    snap = env.snapshot(4)
    env.run("apply", "--plan", env.plan([_action(snap, "閉じてよい", {"state": "closed", "state_reason": "completed"})]))
    code, out = env.run("report")
    assert code == 0
    m = out["metrics"]
    assert m["targets"] == 7 and m["closed"] == 1 and m["verdicts"]["閉じてよい"] == 1
    assert m["wait_count"] == 0


def test_report_sums_reruns_within_round(env):
    s4, s7 = env.snapshot(4), env.snapshot(7)
    close = _action(s4, "閉じてよい", {"state": "closed", "state_reason": "completed"})
    ch = {"state": "closed", "state_reason": "not_planned", "add_labels": ["wontfix"]}
    env.set(lambda st: st.update(throttle=[{"headers": {"Retry-After": "7"}}]))
    code, _ = env.run("apply", "--plan", env.plan([close, _action(s7, "やらない", ch)]))
    assert code == 10
    code, out = env.run("apply", "--plan", env.plan([close, _action(s7, "やらない", ch, approved=True)]))
    assert code == 0 and out["metrics"]["already"] == [4] and out["metrics"]["applied"] == [7]
    code, out = env.run("report")
    m = out["metrics"]
    assert code == 0 and m["applied"] == 2 and m["closed"] == 2 and m["wait_count"] == 1 and m["apply_runs"] == 2


def test_report_after_new_candidates_drops_previous_apply(env):
    snap = env.snapshot(4)
    env.run("apply", "--plan", env.plan([_action(snap, "閉じてよい", {"state": "closed", "state_reason": "completed"})]))
    env.snapshot(3)  # 次の回の candidates
    code, out = env.run("report")
    assert code == 0 and "closed" not in out["metrics"] and "applied" not in out["metrics"]


def test_report_without_records_is_precondition(env):
    p = subprocess.run(
        [PY, str(SCRIPT), "report", "--root", str(env.root), "--repo", "x/none", "--state-dir", str(env.path / "empty")],
        capture_output=True,
        text=True,
        env=dict(os.environ, NDF_UPKEEP_NO_SLEEP="1"),
    )
    assert p.returncode == 3


def test_candidates_do_not_match_a_path_inside_a_longer_path(env):
    env.set(lambda st: st["issues"]["7"].update(body="vendor/scripts/gone-tool.sh と x/gone-tool.sh"))
    _, out = env.run("candidates", "--since-ref", "v1")
    assert 7 not in {i.get("number") for i in out["items"]}


def test_candidates_pick_issues_named_in_commit_subjects(env):
    # 配布に入ったコミットの件名が番号を指す open の課題は、直っていても閉じ忘れのまま残る
    _git(env.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "Fix: 落ちる所を直す (#7 #10)")
    code, out = env.run("candidates", "--since-ref", "v1", "--limit", "1")
    assert code == 20
    first = out["items"][0]
    # 差分のパスより強い手がかりなので、上限で切るときも先に残す
    assert first["number"] == 7 and first["routes"] == ["commit-subject"]
    assert any(t.startswith("Fix: 落ちる所を直す") for t in first["terms"])
    assert out["metrics"]["by_route"]["commit-subject"] == 1  # 閉じた #10 は数えない
