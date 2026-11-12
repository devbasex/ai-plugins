"""別のリポジトリのレーン・計画の所在の一覧・レーン数の上限・レーンの追加・計画の外のセッションの判定（#2733）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import parallel_answer as pa  # noqa: E402
import parallel_files as pf  # noqa: E402
import parallel_guard as pg  # noqa: E402
import parallel_hold as ph  # noqa: E402
import parallel_homes  # noqa: E402
import parallel_lane as pl  # noqa: E402
import parallel_lane_cli as plc  # noqa: E402
import parallel_plan as pp  # noqa: E402
import parallel_release as prl  # noqa: E402
import relay_limit  # noqa: E402

RUNNING = lambda _h, _n: "PENDING"  # noqa: E731


def _git(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    (d / ".ndf").mkdir(exist_ok=True)
    return d.resolve()


def _lane(name: str, issues: list[int], repo: Path | None = None, touches=("a/",)) -> dict:
    ln = {"name": name, "input": {"kind": "sprint", "issues": issues}, "plans": 1, "touches": list(touches)}
    return {**ln, "repo": str(repo)} if repo else ln


def _plan(home: Path, lanes: list[dict], name: str = "p3") -> Path:
    d = pp.plan_dir(home, name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.json").write_text(json.dumps({"version": 1, "name": name, "rules": [], "lanes": lanes}))
    return d


@pytest.fixture
def two_repos(tmp_path):
    x, y = _git(tmp_path / "x"), _git(tmp_path / "y")
    _plan(x, [_lane("la", [1]), _lane("lfactory", [1], repo=y)])
    parallel_homes.record_home(x)
    return x, y


# ---- レーン数の上限（I12） ----


def test_limit_file_then_env_then_default(monkeypatch):
    monkeypatch.delenv("NDF_RELAY_MAX_NAMED", raising=False)
    assert relay_limit.named_cap()[:2] == (3, "default")
    monkeypatch.setenv("NDF_RELAY_MAX_NAMED", "5")
    assert relay_limit.named_cap()[:2] == (5, "env")
    relay_limit.write_max_named(4)
    assert relay_limit.named_cap()[:2] == (4, "file")
    assert pp.named_limit()[0] == 4  # 計画の検査も同じ値を読む
    import relay_lib.hub as hb

    assert hb.max_named() == 4  # リレーハブも同じ値を読む
    with pytest.raises(ValueError):
        relay_limit.write_max_named(0)


def test_relay_cap_cli_writes_and_reads(monkeypatch):
    run = lambda *a: subprocess.run([sys.executable, str(SCRIPTS / "relay.py"), "cap", *a], capture_output=True, text=True)  # noqa: E731
    got = run("--named", "6")
    assert got.returncode == 0, got.stderr
    assert json.loads(got.stdout)["metrics"]["max_named"] == 6
    assert json.loads(run().stdout)["metrics"]["source"] == "file"
    assert run("--named", "x").returncode == 1


# ---- 計画の宣言（I1〜I3・I20） ----


def test_repo_must_be_absolute_main_dir(tmp_path):
    x = _git(tmp_path / "x")
    for bad in ("rel/path", str(tmp_path / "missing")):
        plan, items, _ = pp.check_data({"version": 1, "name": "p", "rules": [], "lanes": [_lane("la", [1], repo=bad)]}, home=x)
        assert plan is None and any(it["rule"] == "I20" for it in items)  # 誤りのある計画は書かせない
    plan, items, _ = pp.check_data({"version": 1, "name": "p", "rules": [], "lanes": [_lane("la", [1], repo=x)]}, home=x)
    assert plan is not None and not [it for it in items if it["level"] == "error"]


def test_overlaps_only_within_one_repo(tmp_path):
    x, y = _git(tmp_path / "x"), _git(tmp_path / "y")
    data = {"version": 1, "name": "p", "rules": [], "lanes": [_lane("la", [1]), _lane("lb", [1], repo=y)]}
    plan, items, _ = pp.check_data(data, home=x)
    assert plan is not None and not any(it["rule"] in ("I19", "I4") for it in items)
    data["lanes"].append({**_lane("lc", [1], repo=y), "handoff": "loop-lc"})
    plan, items, _ = pp.check_data(data, home=x)
    assert plan is None and any(it["rule"] == "I19" for it in items)


def test_render_and_handoff_go_to_lane_repo(tmp_path):
    x, y = _git(tmp_path / "x"), _git(tmp_path / "y")
    raw = {"version": 1, "name": "p", "rules": [], "lanes": [_lane("la", [1]), _lane("lb", [2], repo=y)]}
    plan, _, _ = pp.check_data(raw, home=x)
    written = pp.write_all(x, plan, raw)
    assert (y / ".ndf" / "handoff" / "issue-2.md") in written
    assert str(y) in (pp.plan_dir(x, "p") / "plan.md").read_text()
    assert x in parallel_homes.homes()  # init は所在を書く


# ---- 計画の所在の一覧（I4） ----


def test_homes_skip_missing_plan_dirs(tmp_path):
    parallel_homes.record_home(tmp_path / "gone")
    keep = _git(tmp_path / "keep")
    _plan(keep, [_lane("la", [1])])
    parallel_homes.record_home(keep)
    parallel_homes.record_home(keep)
    assert parallel_homes.homes() == [keep]


# ---- レーンの判定と送受信（受け入れ条件 1・I5・I6・I8） ----


def test_lane_of_finds_plan_in_other_repo(two_repos):
    x, y = two_repos
    got = pl.lane_of(y, "lfactory", status=RUNNING)
    assert got[0] == "p3" and got[1].name == "lfactory"
    assert got.home == x and got.repo == y and got.own_release()
    assert pl.lane_of(x, "lfactory", status=RUNNING) is None  # 宣言のリポジトリと違う場所のセッションは当てない
    assert pl.lane_of(y, "la", status=RUNNING) is None
    same = pl.lane_of(x, "la", status=RUNNING)
    assert same.home == x and not same.own_release()


def test_file_only_lookup_and_plan_home(two_repos):
    x, y = two_repos
    assert pf.lane_homes_in(y, "lfactory", None) == [(x, "p3")]
    assert pf.plan_home(y, "p3", "lfactory") == x
    assert pf.plan_home(y, "zz", "lfactory") == y
    assert pf.lane_plans_in(x, "lfactory", None) == []


def test_lane_in_other_repo_reads_records_at_plan_home(two_repos, monkeypatch):
    """状態の置き場の上書きが無い既定の環境でも、別のリポジトリのレーンは計画の置き場の耐久の記録を読む。"""
    import durable_keys

    x, y = two_repos
    for k in ("NDF_DBOS_DIR", "NDF_STATE_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(y)
    rec = durable_keys.record_path(pl.KIND, pl.record_identity(x, "p3"), pl.record_start(x))
    assert rec.parent != durable_keys.records_dir()  # 作業ディレクトリ（レーンのリポジトリ）の置き場とは違う
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.touch()
    assert pl.record_exists(x, "p3")


def test_questions_carry_lane_repo_from_plan(two_repos):
    x, y = two_repos
    got = pa.with_repo(x, "p3", [{"id": "q1", "lane": "lfactory", "repo": "/forged"}, {"id": "q2", "lane": "la"}])
    assert [q["repo"] for q in got] == [str(y), str(x)]


def test_lane_waits_reads_lane_repo(two_repos):
    x, y = two_repos
    d = y / ".ndf" / "handoff"
    d.mkdir(parents=True)
    (d / "issue-1.md").write_text("待ち: lfactory-r000000000001\n")
    assert pl.lane_waits(x, "p3", "lfactory", "lfactory-r000000000001", lambda: (1, {}))


@pytest.mark.parametrize("condition", ["waiting_only", "answer_hold"])
def test_organizer_reads_loop_record_in_lane_repo(tmp_path, condition):
    """答えを待って止まったループ（答え待ちの停止も）は、答えを受けたら起こす対象になる。"""
    import parallel_route as pr

    x, y = _git(tmp_path / "x"), _git(tmp_path / "y")
    lane = {"name": "lloop", "input": {"kind": "loop", "loop": "lloop"}, "plans": 1, "touches": ["a/"], "repo": str(y)}
    _plan(x, [lane])
    d = y / ".ndf" / "loop" / "lloop"
    d.mkdir(parents=True)
    rec = {"status": "stopped", "stop": {"condition": condition}, "waiting": [{"request": "lloop-g1-abc", "answer": None}]}
    (d / "loop.json").write_text(json.dumps(rec))
    assert pr.waiting_lanes(x, "p3") == ["lloop"]
    assert pr.design_held(x, "p3", ["lloop-g1-abc"]) == ["lloop"]


def test_prior_waits_read_plan_at_home_and_loops_in_lane_repo(tmp_path):
    import gate1_requests as g1

    x, y = _git(tmp_path / "x"), _git(tmp_path / "y")
    lane = {"name": "lloop", "input": {"kind": "loop", "loop": "lloop"}, "plans": 1, "touches": ["a/"], "repo": str(y)}
    _plan(x, [lane])
    for loop in ("lloop", "lold"):
        d = y / ".ndf" / "loop" / loop
        d.mkdir(parents=True)
        (d / "loop.json").write_text(json.dumps({"waiting": [{"number": 4, "pr": 40, "lap": 1}]}))
    rows, skipped = g1.prior_rows(y, "p3", home=x)
    assert [loop for loop, _w in rows] == ["lold"] and not skipped  # 計画は置き場から、ループの記録はレーンのリポジトリから読む


def test_answer_hold_counts_other_repo_lane(two_repos):
    x, y = two_repos
    hold = {"plan": False, "lanes": ["lfactory"], "count": 2, "by_lane": {"lfactory": 2}, "limits": {"plan": 8, "lane": 2}}
    (pp.plan_dir(x, "p3") / ph.HOLD_FILE).write_text(json.dumps(hold))
    got = ph.lane_hold(y, "lfactory", "sprint")
    assert got["count"] == 2 and got["home"] == str(x)


# ---- リリース（受け入れ条件 3・I7） ----


def test_release_mapping_skips_other_repo_lane(two_repos):
    x, _y = two_repos
    plan = pp.load_valid_plan(x, "p3")
    got = prl.map_lanes(plan, [10], {10: [1]}, [], x)
    assert list(got["lanes"]) == ["la"]


# ---- レーンの追加と書き換え（受け入れ条件 7・8・I13） ----


def test_lane_add_over_limit_suggests_cap(monkeypatch, two_repos):
    x, _y = two_repos
    monkeypatch.delenv("NDF_RELAY_MAX_NAMED", raising=False)
    raw = pp.load_plan(x, "p3")
    new, name, err = plc.updated(raw, "add", None, _lane("lc", [3]))
    assert err is None and name == "lc"
    plan, items, _ = pp.check_data({**new, "lanes": [*new["lanes"], _lane("ld", [4])]}, home=x)
    assert plan is None
    hint = plc.cap_hint_items(items, 4)
    assert any("cap --named 4" in (it.get("next") or "") for it in hint)


def test_lane_set_refuses_name_and_repo(two_repos):
    x, _y = two_repos
    raw = pp.load_plan(x, "p3")
    assert plc.updated(raw, "set", "la", {"name": "lb"})[2]
    assert plc.updated(raw, "set", "la", {"repo": "/w"})[2]
    new, _, err = plc.updated(raw, "set", "la", {"input": {"kind": "sprint", "issues": [9]}})
    assert err is None and new["lanes"][0]["input"]["issues"] == [9]


def test_lane_cli_writes_plan_and_handoff(tmp_path, monkeypatch, two_repos):
    x, y = two_repos
    monkeypatch.setenv("NDF_RELAY_MAX_NAMED", "3")
    src = tmp_path / "lane.json"
    src.write_text(json.dumps(_lane("lc", [5], repo=y, touches=["b/"])))
    got = subprocess.run(
        [sys.executable, str(SCRIPTS / "parallel.py"), "lane", "p3", "add", "--from", str(src)], cwd=x, capture_output=True, text=True
    )
    assert got.returncode == 0, got.stdout + got.stderr
    assert [ln["name"] for ln in pp.load_plan(x, "p3")["lanes"]] == ["la", "lfactory", "lc"]
    assert (y / ".ndf" / "handoff" / "issue-5.md").is_file()
    src.write_text(json.dumps(_lane("ld", [6])))
    got = subprocess.run(
        [sys.executable, str(SCRIPTS / "parallel.py"), "lane", "p3", "add", "--from", str(src)], cwd=x, capture_output=True, text=True
    )
    assert got.returncode == 1 and "cap --named 4" in got.stdout
    assert len(pp.load_plan(x, "p3")["lanes"]) == 3  # 何も書かない
    doc = y / ".ndf" / "handoff" / "issue-5.md"
    doc.write_text("作業の状態と利用者の指示\n")
    src.write_text(json.dumps({"touches": ["c/"]}))
    got = subprocess.run(
        [sys.executable, str(SCRIPTS / "parallel.py"), "lane", "p3", "set", "--lane", "lc", "--from", str(src)],
        cwd=x,
        capture_output=True,
        text=True,
    )
    assert got.returncode == 0, got.stdout + got.stderr
    assert doc.read_text() == "作業の状態と利用者の指示\n"  # set は在る引継ぎ文書を残す
    assert not [it for it in json.loads(got.stdout)["items"] if it.get("rule") == "relaunch"]  # 入力を変えなければ起こし直さない
    src.write_text(json.dumps({"input": {"kind": "sprint", "issues": [7]}}))
    got = subprocess.run(
        [sys.executable, str(SCRIPTS / "parallel.py"), "lane", "p3", "set", "--lane", "lc", "--from", str(src)],
        cwd=x,
        capture_output=True,
        text=True,
    )
    assert got.returncode == 0, got.stdout + got.stderr
    assert (y / ".ndf" / "handoff" / "issue-7.md").is_file()  # ループ名を変えたら新しい引継ぎ文書を書く
    relaunch = [it["next"] for it in json.loads(got.stdout)["items"] if it.get("rule") == "relaunch"]
    assert len(relaunch) == 1 and "spawn --name lc" in relaunch[0] and "issue-7.md" in relaunch[0]  # restart でなく新しい入力で起動する


# ---- 計画の外のセッション（受け入れ条件 5・6・18・I9〜I11） ----


def test_guard_blocks_outside_session(two_repos, tmp_path):
    x, _y = two_repos
    other = _git(tmp_path / "z")
    env = {}
    got = pg.judge_entry(other, "loop start", None, env=env, status=RUNNING, is_lane=lambda: False)
    assert got["status"] == "stopped" and got["metrics"]["blocked"] and "--outside-plan" in got["next"]
    assert f"{x}:p3" in got["metrics"]["running"]
    assert not pg.outside_log().exists()


def test_guard_passes_exception_and_logs(two_repos, tmp_path):
    other = _git(tmp_path / "z")
    assert pg.judge_entry(other, "loop start", "急ぎの修正", env={}, status=RUNNING, is_lane=lambda: False) is None
    row = json.loads(pg.outside_log().read_text().splitlines()[-1])
    assert row["reason"] == "急ぎの修正" and row["repo"] == str(other)


def test_guard_passes_without_running_plan_lane_or_factory(two_repos, tmp_path):
    other = _git(tmp_path / "z")
    assert pg.judge_entry(other, "loop start", None, env={}, status=lambda _h, _n: "SUCCESS", is_lane=lambda: False) is None
    assert pg.judge_entry(other, "loop start", None, env={}, status=RUNNING, is_lane=lambda: True) is None
    assert pg.judge_entry(other, "loop start", None, env={"NDF_ORGANIZER_DIR": "/o"}, status=RUNNING, is_lane=lambda: False) is None
    assert pg.judge_entry(other, "loop start", None, env={"NDF_FACTORY_RUN": "1"}, status=RUNNING, is_lane=lambda: False) is None


def test_guard_treats_unreadable_plan_as_running(two_repos, tmp_path):
    other = _git(tmp_path / "z")

    def broken(_h, _n):
        raise RuntimeError("壊れた記録")

    got = pg.judge_entry(other, "loop start", None, env={}, status=broken, is_lane=lambda: False)
    assert got["metrics"]["unreadable"] and got["metrics"]["blocked"]
    assert pg.judge_entry(other, "loop start", "理由", env={}, status=broken, is_lane=lambda: False) is None


def test_guard_passes_organizer_role(two_repos, tmp_path):
    other = _git(tmp_path / "z")
    relay_dir = tmp_path / "relay"
    relay_dir.mkdir()
    (relay_dir / pf.ROLE_FILE).write_text(json.dumps({"role": "organizer", "plan": "p3"}))
    assert pg.judge_entry(other, "loop start", None, env={"NDF_RELAY_DIR": str(relay_dir)}, status=RUNNING, is_lane=lambda: False) is None


def test_loop_start_refuses_empty_reason(tmp_path):
    got = pg.entry_check(str(_git(tmp_path / "z")), "  ", "loop start")
    assert got["status"] == "stopped" and got["exit"] == 1
    assert pg.entry_check(str(tmp_path / "z"), None, "loop start") is None  # 動いている計画が無いマシン


# ---- 第 2 ラウンドの指摘（起こし直しの 1 行・終わった計画の判定と通知） ----


def test_relaunch_line_spawns_even_if_stop_fails(two_repos):
    x, _y = two_repos
    plan, _items, _m = pp.check_data(pp.load_plan(x, "p3"), home=x)
    line = plc.relaunch_items(x, plan, "la", {"input": {"kind": "sprint", "issues": [1]}})[0]["next"]
    got = subprocess.run(
        line.replace("python3 ", "false && echo ", 1).replace(" && python3 ", " && echo spawned ", 1),
        shell=True,
        capture_output=True,
        text=True,
    )
    assert "|| true" in line and "spawned" in got.stdout  # stop が誤りで終わっても spawn を打つ


def test_settled_status_reads_dbos_once_for_finished_plan(two_repos, monkeypatch):
    x, _y = two_repos
    calls = []
    monkeypatch.setattr(pl, "run_status", lambda h, n: calls.append(n) or "SUCCESS")
    assert pl.settled_status(x, "p3") == "SUCCESS" and pl.settled_status(x, "p3") == "SUCCESS"
    assert calls == ["p3"]  # 2 回目はファイルだけで読む
    pl.clear_settled(x, "p3")
    monkeypatch.setattr(pl, "run_status", lambda h, n: calls.append(n) or "PENDING")
    assert pl.settled_status(x, "p3") == "PENDING" and pl.settled_status(x, "p3") == "PENDING"
    assert len(calls) == 3 and not (pp.plan_dir(x, "p3") / pl.DONE_FILE).exists()  # 動いている計画は印を書かない
    assert pg.live_plans(x) == ([f"{x}:p3"], [])


def test_notify_skips_finished_plan(two_repos, monkeypatch):
    x, _y = two_repos
    monkeypatch.setattr(pl, "run_status", lambda h, n: "SUCCESS")
    assert plc.notify(x, "p3") == (False, "SUCCESS")
    pl.clear_settled(x, "p3")
    monkeypatch.setattr(pl, "run_status", lambda h, n: None)
    assert plc.notify(x, "p3") == (False, None)


# ---- 第 3 ラウンドの指摘（例外の起動の引き継ぎ・別のリポジトリの前のループの答え・保留したループの一時レーン） ----


def test_outside_plan_reason_carries_to_sprint_command():
    import sprint_loop_argv as sla

    lap = {"versions": {"dev": ""}, "state_path": "/s.json", "sprint": "s1", "mode": "auto", "out": "/o", "issues": [1]}
    lap["selected"] = [{"number": 1}]
    got = sla.new_sprint_argv("/r", {"outside_plan": "急ぎの修正", "waiting": []}, lap, [])
    assert got[got.index("--outside-plan") + 1] == "急ぎの修正"
    assert "--outside-plan" not in sla.new_sprint_argv("/r", {"waiting": []}, lap, [])


def test_loop_start_reuses_stored_reason(tmp_path):
    import sprint_loop_cli as slc

    root = _git(tmp_path / "z")
    d = root / ".ndf" / "loop" / "l1"
    d.mkdir(parents=True)
    (d / "loop.json").write_text(json.dumps({"name": "l1", "laps": [], "outside_plan": "急ぎの修正"}))
    assert slc.stored_outside_plan(str(root), "l1") == "急ぎの修正"
    assert slc.stored_outside_plan(str(root), "none") is None


def test_prior_request_from_other_repo_lane_routes_to_lane_repo(two_repos, monkeypatch):
    import gate1_requests as g1
    import parallel_reply as prp

    x, y = two_repos
    q = g1.prior_request("lold", {"number": 4, "pr": 40, "lap": 1}, "abc", "t", via="lfactory")
    assert q["via"] == "lfactory" and "lane" not in q
    assert "via" not in g1.prior_request("lold", {"number": 4, "pr": 40, "lap": 1}, "abc", "t")
    assert pa.with_repo(x, "p3", [q])[0]["repo"] == str(y)
    seen = {}
    monkeypatch.setattr(g1, "answer_prior", lambda main, plan_dir, *_a, **_k: seen.update(main=main) or {})
    prp.step_prior_answer(str(x), "p3", q, {"result": "rejected"})
    assert seen["main"] == y


def test_held_loop_helper_spawns_in_loop_repo(tmp_path):
    import parallel_helper as phelp
    import parallel_route as prt

    x, y = _git(tmp_path / "x"), _git(tmp_path / "y")
    lanes = [
        {"name": "lloop", "input": {"kind": "loop", "loop": "lloop"}, "plans": 1, "touches": ["a/"], "repo": str(y)},
        {"name": "lhome", "input": {"kind": "loop", "loop": "lhome"}, "plans": 1, "touches": ["b/"]},
    ]
    _plan(x, lanes)
    assert prt.loop_repo(x, "p3", "lloop") == str(y)
    assert prt.loop_repo(x, "p3", "lhome") is None
    spec = phelp.helper_spawn("p3", 1, 0, pending="lloop", cwd=str(y))
    assert spec["cwd"] == str(y) and "pending lloop" in spec["argv"][-1]  # 起動の耐久ステップがそのリポジトリで打つ（#2936）
    assert phelp.helper_spawn("p3", 1, 0, pending="lhome")["cwd"] is None


def test_loop_start_keeps_reason_in_record(tmp_path):
    import sprint_loop as sl
    import sprint_loop_cli as slc

    root = _git(tmp_path / "z")
    d = root / ".ndf" / "loop" / "l1"
    d.mkdir(parents=True)
    (d / "loop.json").write_text(json.dumps({"name": "l1", "laps": []}))
    env = sl.Env(root)
    slc.keep_outside_plan(env, "l1", " 急ぎの修正 ")
    assert slc.stored_outside_plan(str(root), "l1") == "急ぎの修正"
    slc.keep_outside_plan(env, "none", "理由")  # 記録が無ければ作らない
    assert not (root / ".ndf" / "loop" / "none").exists()


def test_prior_rows_keeps_same_named_loop_of_other_repo(tmp_path):
    import gate1_requests as g1

    x, y = _git(tmp_path / "x"), _git(tmp_path / "y")
    lane = {"name": "lloop", "input": {"kind": "loop", "loop": "lloop"}, "plans": 1, "touches": ["a/"], "repo": str(y)}
    _plan(x, [lane])
    d = x / ".ndf" / "loop" / "lloop"
    d.mkdir(parents=True)
    (d / "loop.json").write_text(json.dumps({"waiting": [{"number": 4, "pr": 40, "lap": 1}]}))
    rows, _ = g1.prior_rows(x, "p3")
    assert [loop for loop, _w in rows] == ["lloop"]  # 現役の lloop は y のレーン。置き場 x の同名は旧ループとして拾う


def test_prior_ids_and_replacement_differ_by_repo(tmp_path):
    import gate1_requests as g1
    import parallel_reply as prp

    x, y, z = (_git(tmp_path / n) for n in "xyz")
    _plan(x, [_lane("la", [1])])
    for r in (x, y, z):
        d = r / ".ndf" / "loop" / "lold"
        d.mkdir(parents=True)
        (d / "loop.json").write_text(json.dumps({"waiting": [{"number": 4, "pr": 40, "lap": 1, "stamp": "s"}]}))
    run = lambda *_a, **_k: (1, "", "")  # noqa: E731
    got = {r: g1.prior_requests(r, "p3", "t", run, home=x)[0][0] for r in (x, y, z)}
    assert got[x]["id"] == "lold-w4-l1" and "scope" not in got[x]  # 置き場のループは従来の id のまま
    assert len({q["id"] for q in got.values()}) == 3 and got[y]["scope"] != got[z]["scope"]
    ar = prp.AnswerRouting.__new__(prp.AnswerRouting)
    ar.routed, ar.questions = True, [got[y]]
    assert "replaced" not in ar.replace_same(got[z]) and ar.questions == [got[y]]  # 別のリポジトリの同名のループは置き換えない
    again = {**got[y], "id": g1.prior_id("lold", 4, 1, "h" * 12, got[y]["scope"])}
    assert ar.replace_same(again)["replaced"][0]["id"] == got[y]["id"] and ar.questions == []


def test_manual_commands_move_to_lane_repo(two_repos):
    x, y = two_repos
    cmds = pp.manual_commands(pp.load_valid_plan(x, "p3"), x)
    assert cmds[0].startswith(f"cd {x} && NDF_RELAY_NAME=la ") and cmds[1].startswith(f"cd {y} && NDF_RELAY_NAME=lfactory ")
