"""upkeep.py rank と、apply・report・candidates の順位付けの部分（#1429 の AC5・AC8〜AC12・AC16・E8）。

gh は PATH の先頭に置いた偽物で置き換える。偽物は FAKE_GH_STATE の JSON を課題とマイルストーンの置き場として読み書きし、
呼ばれた引数を calls に積む。`max_desc` より長い説明の書き込みは 422 で返す。
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
SCRIPT = SKILL / "scripts" / "upkeep.py"
sys.path.insert(0, str(SKILL.parents[1] / "scripts" / "lib"))
from step_result import validate_result  # noqa: E402

PY = sys.executable

FAKE_GH = r"""#!PY
import json, os, re, sys
a = sys.argv[1:]
path = os.environ["FAKE_GH_STATE"]
st = json.load(open(path, encoding="utf-8"))
st.setdefault("calls", []).append(a)
stdin = sys.stdin.read() if "--input" in a else ""

def done(body, code=0, stderr=""):
    json.dump(st, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    if "-i" in a:
        text = "HTTP/2.0 %s\n\n" % ("200 OK" if code == 0 else "422 Unprocessable Entity") + text
    sys.stdout.write(text)
    sys.stderr.write(stderr)
    sys.exit(code)

if a[:2] == ["repo", "view"]:
    done("o/r\n")
rest = [x for x in a[1:] if x not in ("-i", "--paginate")]
target = rest[0]
method = rest[rest.index("-X") + 1] if "-X" in rest else "GET"
issues = st["issues"]
m = re.match(r"repos/o/r/issues\?state=(\w+)", target)
if m:
    done([i for i in issues.values() if i["state"] == m.group(1)])
m = re.match(r"repos/o/r/milestones/(\d+)$", target)
if m and method == "PATCH":
    body = json.loads(stdin)
    if len(body.get("description", "")) > st.get("max_desc", 10**9):
        done({"message": "Validation Failed"}, 1, "gh: Validation Failed (HTTP 422)")
    ms = [x for x in st["milestones"] if x["number"] == int(m.group(1))][0]
    ms.update(body)
    done(ms)
if target.startswith("repos/o/r/milestones"):
    if method == "POST":
        ms = {"number": 100 + len(st["milestones"]), "title": json.loads(stdin)["title"], "description": "", "state": "open"}
        st["milestones"].append(ms)
        done(ms)
    done(st["milestones"])
m = re.match(r"repos/o/r/issues/(\d+)/sub_issues", target)
if m:
    if st.get("no_sub_api"):
        done("", 1, "not found (HTTP 404)")
    done([issues[str(k)] for k in st.get("sub_issues", {}).get(m.group(1), [])])
m = re.match(r"repos/o/r/issues/(\d+)$", target)
if m:
    i = issues[m.group(1)]
    if method == "PATCH":
        patch = json.loads(stdin)
        if "milestone" in patch:
            want = patch.pop("milestone")
            ms = [x for x in st["milestones"] if x["number"] == want]
            i["milestone"] = {"number": ms[0]["number"], "title": ms[0]["title"]} if ms else None
        i.update(patch)
        i["updated_at"] = "2026-09-28T00:00:09Z"
    done(i)
done("", 1, "not found (HTTP 404)")
"""

M1_DESC = (
    "直近の主題\n\n### 並列の組（見込み）\n\n| 組 | 課題 | 触る場所の見込み | 依存 |\n| --- | --- | --- | --- |\n| 1 | #1 #2 | a | なし |\n"
)


def _issue(n, milestone=None, body="", labels=()):
    ms = {"number": {"01 近い": 1, "02 次": 2}[milestone], "title": milestone} if milestone else None
    return {"number": n, "title": f"課題 {n}", "body": body, "state": "open", "milestone": ms,
            "labels": [{"name": x} for x in labels], "updated_at": "2026-09-01T00:00:00Z"}  # fmt: skip


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args], check=True, capture_output=True)


@pytest.fixture
def env(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("a\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "tag", "v1")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH.replace("#!PY", "#!" + PY))
    (bindir / "gh").chmod(0o755)
    state = {
        "issues": {str(i["number"]): i for i in (_issue(1, "01 近い"), _issue(2, "01 近い"), _issue(3, "02 次"), _issue(4))},
        "milestones": [
            {"number": 1, "title": "01 近い", "description": M1_DESC, "state": "open"},
            {"number": 2, "title": "02 次", "description": "次の主題", "state": "open"},
        ],
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
        sd = tmp_path / "state" / "o--r"

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

        def scores(self, specs, name="scores.json"):
            f = tmp_path / name
            f.write_text(json.dumps({"version": 1, "issues": specs}, ensure_ascii=False))
            return str(f)

        def plan(self, actions, **kw):
            f = tmp_path / "plan.json"
            f.write_text(json.dumps({"repo": "o/r", "actions": actions, **kw}, ensure_ascii=False))
            return str(f)

        def snapshots(self):
            """candidates の値（candidates は rank.json を消すため、rank の前に取る）。"""
            _, out = self.run("candidates", "--since-ref", "v1", "--all")
            return {i["number"]: i for i in out["items"] if i.get("number")}

        def writes(self):
            return [c for c in self.state()["calls"] if "-X" in c]

    return Env()


BANDS = {1: "なし", 2: "表示", 3: "表示", 5: "機能", 8: "機能", 13: "全体", 20: "全体"}


def sc(n, ubv=3, tc=1, rr=1, size=1, **kw):
    return {"number": n, "digest": f"d{n}", "ubv": {"step": ubv, "why": "根拠", "impact": BANDS[ubv]}, "tc": {"step": tc, "why": "根拠"},
            "rr_oe": {"step": rr, "why": "根拠"}, "size": {"step": size, "why": "実績と比べた"}, **kw}  # fmt: skip


BASE = [sc(1, tc=5, size=1), sc(2, tc=1, size=2), sc(3, ubv=5, tc=3, size=3), sc(4, ubv=13, tc=8, rr=8, size=5, observed_harm=True)]


def _reschedule(snaps, rank_out, n, direction, approved=False):
    snap = snaps[n]
    c = next(x for x in rank_out["metrics"]["forward" if direction == "前倒し" else "backward"] if x["number"] == n)
    return {"number": n, "verdict": "そのまま", "updated_at": snap["updated_at"], "digest": snap["digest"],
            "changes": {"milestone": c["to"]}, "reschedule": direction, "approved": approved}  # fmt: skip


# ---------------- rank ----------------


def test_rank_without_milestones_is_skipped(env):
    env.set(lambda st: st.update(milestones=[]))
    code, out = env.run("rank")
    assert code == 0 and out["metrics"]["skipped"] is True


def test_unscored_issues_are_excluded_and_capacity_is_asked_for(env):
    code, out = env.run("rank")
    assert code == 20  # status は validate_result が終了コードと照らす
    assert {e["number"] for e in out["metrics"]["excluded"]} == {1, 2, 3, 4}
    code, out = env.run("rank", "--scores", env.scores(BASE))
    assert code == 0 and "--capacity" in out["next"]
    assert out["metrics"]["forward"] == [] and all(m["boundary"] is None for m in out["metrics"]["milestones"])
    # 記録に残った見積は次の回の入力が無くても使う
    code, out = env.run("rank")
    assert code == 0 and [r["number"] for r in out["metrics"]["milestones"][0]["rows"]] == [1, 2]


def test_rank_reads_the_declaration_and_arguments_win(env):
    nd = env.root / ".ndf"
    nd.mkdir()
    (nd / "backlog.json").write_text(json.dumps({"version": 1, "capacity": 3}))
    _, out = env.run("rank", "--scores", env.scores(BASE))
    assert out["metrics"]["capacity"] == 3 and out["metrics"]["forward"]
    _, out = env.run("rank", "--capacity", "20")
    assert out["metrics"]["capacity"] == 20
    (nd / "backlog.json").write_text("{")
    code, out = env.run("rank")
    assert code == 2


def test_rank_stops_on_a_cycle(env):
    code, out = env.run("rank", "--scores", env.scores([sc(1, depends_on=[2]), sc(2, depends_on=[1]), sc(3), sc(4)]))
    assert code == 2 and out["status"] == "stopped" and out["metrics"]["cycle"] == [1, 2]


def test_rank_without_sub_issue_api_uses_other_sources(env):
    env.set(lambda st: (st.update(no_sub_api=True), st["issues"]["1"].update(sub_issues_summary={"total": 1})))
    code, out = env.run("rank", "--scores", env.scores(BASE))
    assert code == 0 and any("サブイシューの API" in n for n in out["metrics"]["notes"])


# ---------------- apply ----------------


def test_apply_moves_auto_holds_approval_and_writes_the_table_once(env):
    snaps = env.snapshots()
    _, first = env.run("rank", "--scores", env.scores(BASE), "--capacity", "8")
    fwd = {f["number"]: f for f in first["metrics"]["forward"]}
    assert fwd[4]["decision"] == "自動" and fwd[3]["decision"] == "承認"
    assert [b["number"] for b in first["metrics"]["backward"]] == [2]
    actions = [
        _reschedule(snaps, first, 4, "前倒し"),
        _reschedule(snaps, first, 3, "前倒し", approved=True),
        _reschedule(snaps, first, 2, "後ろ倒し"),
    ]
    code, out = env.run("apply", "--plan", env.plan(actions, rank=first["metrics"]["digest"]))
    assert code == 10, out
    assert out["metrics"]["applied"] == [4] and out["metrics"]["needs_approval"] == [3, 2]
    pres = Path(out["presentation_path"]).read_text(encoding="utf-8")
    assert "#3（前倒し: 02 次 → 01 近い）" in pres and "#2（後ろ倒し: 01 近い → 02 次）" in pres
    st = env.state()
    assert st["issues"]["4"]["milestone"]["title"] == "01 近い" and st["issues"]["3"]["milestone"]["title"] == "02 次"
    m1 = next(m for m in st["milestones"] if m["number"] == 1)
    assert m1["title"] == "01 近い" and m1["description"].startswith(M1_DESC)  # 題名と節の外は変えない（AC10・AC11）
    assert "### 順位" in m1["description"] and "| 1 | #4 |" in m1["description"]
    assert all("title" not in json.dumps(c) for c in st["calls"] if "milestones/1" in " ".join(c))
    before = len(env.writes())
    code, out = env.run("apply", "--plan", env.plan([], rank=first["metrics"]["digest"]))
    assert code == 0 and out["metrics"]["tables"]["written"] == [] and len(env.writes()) == before

    # 承認を得た移動は rank --approved で打ち直してから反映する（AC9）
    snaps = env.snapshots()
    _, second = env.run("rank", "--approved", "3,2", "--capacity", "8")
    decisions = {(c["number"], c["decision"]) for c in second["metrics"]["forward"] + second["metrics"]["backward"]}
    assert (3, "承認済み") in decisions and (2, "承認済み") in decisions
    actions = [_reschedule(snaps, second, 3, "前倒し"), _reschedule(snaps, second, 2, "後ろ倒し")]
    code, out = env.run("apply", "--plan", env.plan(actions, rank=second["metrics"]["digest"]))
    assert code == 0 and sorted(out["metrics"]["applied"]) == [2, 3]


def test_apply_refuses_a_stale_rank_and_unknown_directions(env):
    snaps = env.snapshots()
    _, first = env.run("rank", "--scores", env.scores(BASE), "--capacity", "8")
    code, out = env.run("apply", "--plan", env.plan([_reschedule(snaps, first, 4, "前倒し")], rank="0000"))
    assert code == 20 and env.writes() == []
    act = {**_reschedule(snaps, first, 4, "前倒し"), "reschedule": "forward"}
    code, _ = env.run("apply", "--plan", env.plan([act]))
    assert code == 2
    act = {**_reschedule(snaps, first, 4, "前倒し"), "changes": {"milestone": "02 次"}}
    code, _ = env.run("apply", "--plan", env.plan([act]))
    assert code == 2


def test_apply_refuses_a_forward_move_that_leaves_its_group_behind(env):
    snaps = env.snapshots()
    scores = [
        sc(1, tc=5, size=1),
        sc(2, tc=1, size=2),
        sc(3, size=3),
        sc(4, ubv=13, tc=8, rr=8, size=5, observed_harm=True, depends_on=[3]),
    ]
    _, first = env.run("rank", "--scores", env.scores(scores), "--capacity", "8")
    fwd = {f["number"]: f for f in first["metrics"]["forward"]}
    assert fwd[4]["group"] == [3]
    lead = _reschedule(snaps, first, 4, "前倒し")
    code, out = env.run("apply", "--plan", env.plan([lead], rank=first["metrics"]["digest"]))
    assert code == 2 and "#3" in out["summary"] and env.writes() == []
    member = {**lead, "number": 3, "updated_at": snaps[3]["updated_at"], "digest": snaps[3]["digest"]}
    code, out = env.run("apply", "--plan", env.plan([lead, member], rank=first["metrics"]["digest"]))
    assert code != 2, out


def test_rejected_move_is_remembered_by_apply_only(env):
    _, first = env.run("rank", "--scores", env.scores(BASE), "--capacity", "8")
    code, _ = env.run("apply", "--plan", env.plan([], rank=first["metrics"]["digest"], rejected=[{"number": 3, "direction": "前倒し"}]))
    assert code == 0
    saved = json.loads((env.sd / "rejected.json").read_text())
    assert [(r["number"], r["direction"]) for r in saved] == [(3, "前倒し")]
    _, again = env.run("rank", "--capacity", "8")
    assert 3 not in [f["number"] for f in again["metrics"]["forward"]] and again["metrics"]["rejected"][0]["number"] == 3
    assert json.loads((env.sd / "rejected.json").read_text()) == saved  # rank は書かない
    code, _ = env.run("apply", "--plan", env.plan([], rank=first["metrics"]["digest"], rejected=[{"number": 3, "direction": "forward"}]))
    assert code == 2


def test_long_table_is_halved_until_accepted(env):
    specs = [sc(n, size=1) for n in range(1, 5)]
    env.set(lambda st: [st["issues"][str(n)].update(milestone={"number": 1, "title": "01 近い"}) for n in (3, 4)])
    _, first = env.run("rank", "--scores", env.scores(specs))
    env.set(lambda st: st.update(max_desc=len(M1_DESC) + 260))
    code, out = env.run("apply", "--plan", env.plan([], rank=first["metrics"]["digest"]))
    assert code == 0 and out["metrics"]["tables"]["truncated"]["01 近い"] in (1, 2), out
    desc = env.state()["milestones"][0]["description"]
    assert "件は表に載せていない" in desc


# ---------------- report・candidates ----------------


def test_report_shows_changes_from_the_previous_table(env):
    _, first = env.run("rank", "--scores", env.scores(BASE))
    env.run("apply", "--plan", env.plan([], rank=first["metrics"]["digest"]))
    _, second = env.run("rank", "--scores", env.scores([sc(2, ubv=13, tc=8, size=2)], "s2.json"))
    ch = {(c["number"], c["kind"]): c for c in second["metrics"]["changes"]}
    assert ch[(2, "up")]["columns"] == ["UBV 3→13", "TC 1→8"] and (1, "down") in ch
    code, rep = env.run("report")
    assert code == 0 and "UBV 3→13" in next(i["value"] for i in rep["items"] if i["name"] == "前回からの変化")


def test_waiting_decision_and_needs_detail_are_reported(env):
    specs = [*BASE[:3], {**BASE[3], "waiting_decision": True, "needs_detail": ["size"]}]
    env.run("rank", "--scores", env.scores(specs))
    _, rep = env.run("report")
    assert rep["metrics"]["waiting_decision"] == [4] and rep["metrics"]["needs_detail"] == {"4": ["size"]}


def test_candidates_pick_unscored_issues_only_after_rank(env):
    _, out = env.run("candidates", "--since-ref", "v1")
    assert out["metrics"]["by_route"]["unscored"] == 0
    snaps = {i["number"]: i["digest"] for i in env.run("candidates", "--since-ref", "v1", "--all")[1]["items"]}
    env.run("rank", "--scores", env.scores([{**s, "digest": snaps[s["number"]]} for s in BASE[:3]]))
    env.set(lambda st: st["issues"]["1"].update(body="変わった本文"))
    _, out = env.run("candidates", "--since-ref", "v1")
    got = {i["number"] for i in out["items"] if "unscored" in i.get("routes", [])}
    assert got == {1, 4}  # 本文の変わった #1 と、見積の無い #4
    assert not (env.sd / "rank.json").exists()


def test_apply_does_not_write_the_table_when_a_planned_move_fails(env):
    """予定どおりに移せなかった課題があれば、順位の表を書かずに止める（表と課題の所属を食い違わせない）。"""
    snaps = env.snapshots()
    scores = [
        sc(1, tc=5, size=1),
        sc(2, tc=1, size=2),
        sc(3, size=3),
        sc(4, ubv=13, tc=8, rr=8, size=5, observed_harm=True, depends_on=[3]),
    ]
    _, first = env.run("rank", "--scores", env.scores(scores), "--capacity", "8")
    lead = _reschedule(snaps, first, 4, "前倒し")
    member = {**lead, "number": 3, "updated_at": snaps[3]["updated_at"], "digest": snaps[3]["digest"]}
    env.set(lambda st: st["issues"]["3"].update(updated_at="2026-09-20T00:00:00Z", body="変わった"))
    code, out = env.run("apply", "--plan", env.plan([lead, member], rank=first["metrics"]["digest"]))
    assert code == 20 and out["metrics"]["skipped_changed"] == [3], out
    assert out["metrics"]["tables_withheld"] == [3] and out["metrics"]["tables"]["written"] == []
    assert "### 順位" not in env.state()["milestones"][0]["description"]
