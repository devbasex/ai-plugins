"""並列の計画の検査と作成（#2436 の受け入れ条件 1〜3、I1〜I5・I19）と、リレーハブの数え方（受け入れ条件 7、I13）。"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import parallel_plan as pp  # noqa: E402
import parallel_release as prl  # noqa: E402
from relay_lib import hub as hb  # noqa: E402

BASE = {
    "version": 1,
    "name": "p1",
    "rules": ["取り込みの衝突は base-sync で解く"],
    "lanes": [
        {
            "name": "lroot",
            "input": {"kind": "loop", "loop": "lroot", "args": ["--milestones", "26"]},
            "plans": 2,
            "touches": ["plugins/ndf/"],
        },
        {"name": "lflow", "input": {"kind": "loop", "loop": "lflow", "args": ["--milestones", "23"]}, "plans": 2, "touches": ["docs/"]},
        {"name": "lfac", "input": {"kind": "sprint", "issues": [2400]}, "plans": 2, "touches": ["web/"], "after": ["lflow"]},
    ],
}


def rules_of(data):
    _, items, _ = pp.check_data(data)
    return {(it["level"], it["rule"]) for it in items}


def variant(**edit):
    d = copy.deepcopy(BASE)
    for k, v in edit.items():
        v(d) if callable(v) else d.__setitem__(k, v)
    return d


def test_valid_plan_passes():
    plan, items, metrics = pp.check_data(BASE)
    assert plan is not None and metrics["errors"] == 0 and items == []


@pytest.mark.parametrize(
    "data,rule",
    [
        (variant(name="a" * 25), "I1"),
        (variant(x=lambda d: d["lanes"][0].__setitem__("name", "p1-rel1")), "I1"),
        (variant(x=lambda d: d["lanes"][1].__setitem__("name", "lroot")), "I1"),
        (variant(x=lambda d: d["lanes"][2].__setitem__("handoff", "loop-lflow")), "I1"),
        (variant(x=lambda d: d["lanes"][0].__setitem__("plans", 3)), "I2"),
        (variant(x=lambda d: d["lanes"].append({**d["lanes"][0], "name": "l4", "plans": 0})), "form"),
        (variant(x=lambda d: d["lanes"][2].__setitem__("after", ["nope"])), "I3"),
        (variant(x=lambda d: d["lanes"][1].__setitem__("after", ["lfac"])), "I3"),
        (variant(x=lambda d: d["lanes"][1]["input"].__setitem__("args", [])), "I19"),
        (variant(x=lambda d: d["lanes"][1]["input"].__setitem__("args", ["--milestones", "26"])), "I19"),
    ],
)
def test_errors_carry_rule(data, rule):
    assert ("error", rule) in rules_of(data)


def test_name_at_limit_passes():
    assert pp.check_data(variant(name="a" * 24))[0] is not None


def test_total_plans_boundary(monkeypatch):
    assert pp.check_data(BASE)[0] is not None  # 合計 6 は上限ちょうど
    monkeypatch.setenv("NDF_PARALLEL_MAX_PLANS", "5")
    assert ("error", "I2") in rules_of(BASE)
    monkeypatch.setenv("NDF_PARALLEL_MAX_PLANS", "zero")
    _, _, metrics = pp.check_data(BASE)
    assert metrics["max_plans"] == 6 and metrics["notes"]


def test_lane_count_over_named_cap(monkeypatch):
    monkeypatch.setenv("NDF_RELAY_MAX_NAMED", "2")
    assert ("error", "I2") in rules_of(BASE)


def test_touch_overlap_is_warning():
    d = variant(x=lambda d: d["lanes"][1].__setitem__("touches", ["plugins/ndf/skills/"]))
    plan, items, metrics = pp.check_data(d)
    assert plan is not None and metrics["errors"] == 0 and ("warning", "I4") in {(i["level"], i["rule"]) for i in items}


def test_ready_lanes_waits_for_dependency():
    plan = pp.check_data(BASE)[0]
    assert [ln.name for ln in pp.ready_lanes(plan, set(), set())] == ["lroot", "lflow"]
    assert [ln.name for ln in pp.ready_lanes(plan, {"lroot", "lflow"}, {"lflow"})] == ["lfac"]


def test_a_sprint_lane_ends_on_its_end_record_and_a_loop_lane_keeps_its_end():
    """1 スプリントのレーンの最初の入力は終わりの記録の行を「終わり」に持ち、開発ループのレーンは今のまま（#3058 の A1・A8）。"""
    sprint = pp.Lane(name="la", input={"kind": "sprint", "issues": [1, 2]}, plans=1, touches=[])
    loop = pp.Lane(name="lc", input={"kind": "loop", "loop": "main", "args": ["--milestones=m1"]}, plans=1, touches=[])
    assert sprint.start_command("p1") == "/goal /ndf:development-workflow #1 #2 -- 終わり: [ndf-parallel] lane-end p1 la の行を出したとき"
    assert loop.start_command("p1") == (
        "/goal /ndf:development-workflow --loop main --milestones=m1 -- 終わり: [ndf-loop] stopped main の行を出したとき"
    )


def _git_main(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".ndf").mkdir()  # 引継ぎ文書の置き場（development-workflow の手順 0 が作る）
    return tmp_path


def _cli(cwd: Path, *args: str, stdin: str | None = None):
    got = subprocess.run([sys.executable, str(SCRIPTS / "parallel.py"), *args], cwd=cwd, input=stdin, capture_output=True, text=True)
    return got.returncode, json.loads(got.stdout.strip().splitlines()[-1])


def test_init_writes_plan_and_handoffs_without_copying_rules(tmp_path):
    main = _git_main(tmp_path)
    code, res = _cli(main, "init", "p1", stdin=json.dumps(BASE))
    assert code == 0, res
    d = main / ".ndf" / "parallel" / "p1"
    assert (d / "plan.json").is_file() and (d / "plan.md").is_file()
    docs = [main / ".ndf" / "handoff" / f for f in ("loop-lroot.md", "loop-lflow.md", "issue-2400.md")]
    for p in docs:
        text = p.read_text()
        assert str(d / "plan.md") in text and BASE["rules"][0] not in text
    assert json.loads((d / "plan.json").read_text())["lanes"][2]["handoff"] == "issue-2400"
    code, res = _cli(main, "check", "p1")
    assert code == 0 and res["metrics"]["errors"] == 0


def test_init_refuses_existing_handoff_and_writes_nothing(tmp_path):
    main = _git_main(tmp_path)
    (main / ".ndf" / "handoff").mkdir(parents=True)
    (main / ".ndf" / "handoff" / "loop-lflow.md").write_text("前からある\n")
    code, res = _cli(main, "init", "p1", stdin=json.dumps(BASE))
    assert code == 1 and "loop-lflow.md" in res["summary"]
    assert not (main / ".ndf" / "parallel" / "p1").exists()
    assert not (main / ".ndf" / "handoff" / "loop-lroot.md").exists()
    assert (main / ".ndf" / "handoff" / "loop-lflow.md").read_text() == "前からある\n"


def test_init_with_error_writes_nothing(tmp_path):
    main = _git_main(tmp_path)
    code, res = _cli(main, "init", "p1", stdin=json.dumps(variant(x=lambda d: d["lanes"][1].__setitem__("after", ["lfac"]))))
    assert code == 1 and any(i["rule"] == "I3" for i in res["items"])
    assert not (main / ".ndf" / "parallel").exists() and not (main / ".ndf" / "handoff").exists()


def test_run_outside_wrapper_returns_manual_lines(tmp_path, monkeypatch):
    main = _git_main(tmp_path)
    assert _cli(main, "init", "p1", stdin=json.dumps(BASE))[0] == 0
    monkeypatch.delenv("NDF_RELAY_DIR", raising=False)
    code, res = _cli(main, "run", "p1")
    assert code == 3 and res["metrics"]["manual"] is True
    assert [i["lane"] for i in res["items"]] == ["lroot", "lflow", "lfac"]


# ---- リレーハブの数え方（I13） ----


def _w(name, role=None):
    return hb.Wrapper(f"/x/{name}", name, True, False, None, None, role)


def test_organizer_is_not_counted_once():
    now = hb.live([_w("org", "organizer"), _w("a"), _w("b"), _w("c")], {}, {})
    assert now.count == 3
    assert hb.live([_w("o1", "organizer"), _w("o2", "organizer"), _w("a")], {}, {}).count == 2


def test_helper_passes_once_over_cap(monkeypatch):
    monkeypatch.setattr(hb.tmux, "binary", lambda: "/usr/bin/tmux")
    monkeypatch.setenv("NDF_RELAY_MAX_NAMED", "3")
    full = hb.live([_w("org", "organizer"), _w("a"), _w("b"), _w("c")], {}, {})
    assert hb.Hub.judge("p1-rel1", full, "helper") is None
    assert hb.Hub.judge("d", full) is not None
    with_helper = hb.live([_w("a"), _w("b"), _w("c"), _w("p1-rel1", "helper")], {}, {})
    assert with_helper.count == 3 and hb.Hub.judge("p1-rel2", with_helper, "helper") is not None


def test_registry_role_counts_before_role_file():
    now = hb.live([_w("a"), _w("p1-rel1")], {"p1-rel1": {"role": "helper"}}, {})
    assert now.count == 1 and now.helpers == 1


# ---- まとめたリリース（I9・I14・I15） ----


def test_range_maps_unrequested_lane_and_orphans():
    plan = pp.check_data(BASE)[0]
    reqs = [{"id": "lroot-r1", "lane": "lroot", "prs": [10]}]
    got = prl.map_lanes(plan, [10, 11, 12], {11: [2400], 12: []}, reqs)
    assert got["lanes"]["lroot"]["requested"] is True and got["lanes"]["lfac"] == {"prs": [11], "requested": False, "requests": []}
    assert got["orphans"] == [12]


def test_answerable_and_moved():
    pending = [{"id": "a", "prs": [1]}, {"id": "b", "prs": [5]}]
    assert prl.answerable(pending, [1, 2]) == ["a"]
    # 一部だけが範囲に入る依頼は答えない（残りを出さずにリリース済みと答えない）
    assert prl.answerable([{"id": "c", "prs": [1, 3]}, {"id": "d", "prs": []}], [1, 2]) == []
    # 先の PR が前のリリースで出た依頼は、今回の範囲と合わせて全部が入れば答える。前の範囲だけの依頼はこのリリースで答えない
    assert prl.answerable([{"id": "e", "prs": [1, 3]}, {"id": "f", "prs": [3]}], [1, 2], done=[3]) == ["e"]
    assert prl.moved("abc", "abc") is False and prl.moved("abc", "def") is True


@pytest.mark.parametrize("name,ok", [("loop-lroot-lane", True), ("sprint-p3", True), ("lane-lroot", False), ("loop-lroot-history", False)])
def test_handoff_name_follows_handoff_py(name, ok):
    """レーンの引継ぎ文書の名は handoff.py の名の形だけを受ける（handoff.py の副命令で扱えない名を作らない）。"""
    plan, _, metrics = pp.check_data(variant(x=lambda d: d["lanes"][0].__setitem__("handoff", name)))
    assert (plan is not None) is ok
    assert (metrics["errors"] == 0) is ok


@pytest.fixture()
def run_proc():
    """`parallel.py run p1` の起動の引数を持つ、動き続けるプロセス（`run.pid` の確かめが読む。#2647 の決定 9）。"""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", "parallel.py", "run", "p1"])
    yield p
    p.kill()
    p.wait()


def _running_plan(tmp_path: Path, pid: int) -> tuple[Path, dict]:
    """run が動いている（`run.pid` の pid が生きている）計画と、ラッパーの中のセッションの環境。"""
    (tmp_path / "repo").mkdir()
    main = _git_main(tmp_path / "repo")
    assert _cli(main, "init", "p1", stdin=json.dumps(BASE))[0] == 0
    (pp.plan_dir(main, "p1") / "run.pid").write_text(f"{pid}\n")
    return main, {k: v for k, v in os.environ.items() if k != "NDF_PARALLEL_RELAY"}


def test_start_on_a_running_plan_marks_the_session_as_organizer(tmp_path, run_proc):
    """動いている run でも、`start` を打ったセッションのラッパーへオーガナイザーの印を付ける（#2637）。"""
    main, env = _running_plan(tmp_path, run_proc.pid)
    relay_dir = tmp_path / "wrapper"
    relay_dir.mkdir()
    got = subprocess.run(
        [sys.executable, str(SCRIPTS / "parallel.py"), "start", "p1"],
        cwd=main, capture_output=True, text=True, env={**env, "NDF_RELAY_DIR": str(relay_dir)},
    )  # fmt: skip
    res = json.loads(got.stdout.strip().splitlines()[-1])
    assert got.returncode == 0 and res["metrics"]["pid"] == run_proc.pid and res["metrics"]["organizer"] is True
    role = json.loads((relay_dir / "role.json").read_text())
    assert (role["role"], role["plan"]) == ("organizer", "p1")


def test_start_divides_the_issue_candidate_by_the_lanes_of_the_plan(tmp_path, run_proc):
    main, env = _running_plan(tmp_path, run_proc.pid)
    env.pop("NDF_RELAY_DIR", None)
    got = subprocess.run([sys.executable, str(SCRIPTS / "parallel.py"), "start", "p1"], cwd=main, capture_output=True, text=True, env=env)
    assert got.stdout, got.stderr
    report = json.loads(got.stdout.strip().splitlines()[-1])["metrics"]["environment"]
    lanes = len(BASE["lanes"])
    assert report["lanes"] == lanes and report["candidates"]["max_issues"] == max(1, report["candidates"]["max_plans"] // lanes)


def test_start_on_a_running_plan_outside_a_wrapper_still_answers(tmp_path, run_proc):
    """ラッパーの外では印を付けられないが、`start` の答えは今のまま（次の 1 手を返す）で、run を増やさない。"""
    main, env = _running_plan(tmp_path, run_proc.pid)
    env.pop("NDF_RELAY_DIR", None)
    got = subprocess.run([sys.executable, str(SCRIPTS / "parallel.py"), "start", "p1"], cwd=main, capture_output=True, text=True, env=env)
    res = json.loads(got.stdout.strip().splitlines()[-1])
    assert got.returncode == 0 and res["metrics"]["organizer"] is False and res["metrics"]["pid"] == run_proc.pid
    assert (pp.plan_dir(main, "p1") / "run.pid").read_text().strip() == str(run_proc.pid)


# ---- 起動の前のリリースの検査（#2616 の受け入れ条件 16・17） ----

AUTO = [{"kind": "auto", "channel": "base", "target": "stg"}, {"kind": "auto", "channel": "production", "target": "prod"}]


def _declare(main, delivery=None, form=False):
    (main / ".ndf" / "worktree.json").write_text(json.dumps({"base_branch": "develop", "production_branch": "main"}))
    if delivery is not None:
        (main / ".ndf" / "project.json").write_text(json.dumps({"delivery": delivery}))
    if form:
        (main / ".ndf" / "supervise.json").write_text(json.dumps({"release": {"form": "package-plugin", "plugin": "ndf"}}))


@pytest.mark.parametrize(
    "rows, form, at",
    [
        ([{"kind": "manual", "target": "prod", "trigger": "手で流す"}], False, "delivery"),
        (None, False, "delivery"),
        ({"unknown": "調べていない"}, False, "delivery"),
        (None, True, "release.plugin"),
    ],
)
def test_check_items_warns_when_a_release_cannot_be_built(tmp_path, monkeypatch, rows, form, at):
    import parallel_route as prt

    monkeypatch.setattr(prt, "dev_version", lambda _m: "")
    _declare(_git_main(tmp_path), rows, form)
    got = prt.check_items(tmp_path)
    assert [(g["level"], g["rule"], g["at"]) for g in got] == [("warning", "release", at)] and got[0]["message"]


@pytest.mark.parametrize("rows, form", [(AUTO, False), (None, True)])
def test_check_items_is_silent_when_a_release_can_be_answered(tmp_path, monkeypatch, rows, form):
    import parallel_route as prt

    monkeypatch.setattr(prt, "dev_version", lambda _m: "1.2.3")
    _declare(_git_main(tmp_path), rows, form)
    assert prt.check_items(tmp_path) == []


def test_check_cli_counts_the_warning_and_exits_zero(tmp_path):
    main = _git_main(tmp_path)
    assert _cli(main, "init", "p1", stdin=json.dumps(BASE))[0] == 0
    code, res = _cli(main, "check", "p1")
    assert code == 0 and [i["at"] for i in res["items"] if i.get("rule") == "release"] == ["delivery"]
    warned = res["metrics"]["warnings"]
    _declare(main, AUTO)
    code, res = _cli(main, "check", "p1")
    assert code == 0 and res["metrics"]["warnings"] == warned - 1 and all(i.get("rule") != "release" for i in res["items"])


def test_a_reused_run_pid_is_not_read_as_a_running_run(tmp_path, run_proc):
    """`run.pid` の pid が `parallel.py run <計画名>` 以外のプロセスに使い回されていれば、動いていると読まない（#2647 の条件 15）。"""
    import parallel_lane as pl

    pp.plan_dir(tmp_path, "p1").mkdir(parents=True)
    (pp.plan_dir(tmp_path, "p1") / "run.pid").write_text(f"{os.getpid()}\n")
    assert pl.live_run_pid(tmp_path, "p1") is None
    (pp.plan_dir(tmp_path, "p1") / "run.pid").write_text(f"{run_proc.pid}\n")
    assert pl.live_run_pid(tmp_path, "p1") == run_proc.pid


class _Reply(Exception):
    pass


def test_start_with_a_dead_run_pid_marks_the_session_before_continuing(tmp_path, monkeypatch):
    """`run.pid` の pid が死んでいて耐久ワークフローが途中の計画でも、`start` が打ったセッションへ印を付けて続ける（#2647 の条件 15）。"""
    import parallel_cli as pcli
    import parallel_lane as pl
    import parallel_run as pr_run

    main = _git_main(tmp_path)
    assert _cli(main, "init", "p1", stdin=json.dumps(BASE))[0] == 0
    marked, spawned, out = [], [], {}
    monkeypatch.setattr(pr_run, "mark_organizer", lambda m, n, clear=False: marked.append((n, clear)) or True)
    monkeypatch.setattr(pr_run, "environment_report", lambda m, p: ({}, []))
    monkeypatch.setattr(pl, "live_run_pid", lambda m, n: None)
    monkeypatch.setattr(pl, "run_status", lambda m, n: "PENDING")
    monkeypatch.setattr(pcli.subprocess, "Popen", lambda argv, **kw: spawned.append(argv) or type("P", (), {"pid": 4321})())

    def reply(status, summary, items=None, code=None, **metrics):
        out.update(status=status, metrics=metrics)
        raise _Reply

    def checked(m, n):
        return pp.check_data(pp.load_plan(m, n), home=m)

    a = type("A", (), {"name": "p1"})()
    with pytest.raises(_Reply):
        pcli.start_plan(a, reply, main, checked, SCRIPTS / "parallel.py")
    assert marked == [("p1", False)] and out["metrics"]["organizer"] is True
    assert spawned and spawned[0][-2:] == ["run", "p1"]
