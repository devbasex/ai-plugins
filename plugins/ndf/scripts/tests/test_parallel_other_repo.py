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
        _, items, _ = pp.check_data({"version": 1, "name": "p", "rules": [], "lanes": [_lane("la", [1], repo=bad)]}, home=x)
        assert any(it["rule"] == "I20" for it in items)
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
