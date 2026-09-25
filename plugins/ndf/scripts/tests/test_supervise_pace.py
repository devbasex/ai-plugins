"""supervise.py の `pace: fast`（#1078）: 計画の組み立て（new mission --pace fast・new check --since-last・
new close・new release --mvv・new impl --escape-of）と実行（実行の条件・--then の段・{queue_pr:<名>}・gate_as_ok）。

実機の claude と gh は呼ばない（計画の形と、run の段だけの計画を流して見る）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SUPERVISE = SCRIPTS / "supervise.py"
MISSION_STATE = SCRIPTS / "mission-state.py"
REPO = SCRIPTS.parents[2]
PY = sys.executable

spec = importlib.util.spec_from_file_location("supervise_pace", SUPERVISE)
sv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sv)


def cli(*args, cwd=None):
    return subprocess.run([PY, str(SUPERVISE), *args], capture_output=True, text=True, cwd=cwd or REPO)


def mission_state(tmp_path: Path, approve: bool = True) -> Path:
    mvv = tmp_path / "mvv-src.md"
    mvv.write_text("## Mission\n速く\n## Vision\n回る\n## Value\n実測\n")
    state = tmp_path / "state" / "mission-state.json"
    subprocess.run([PY, str(MISSION_STATE), "init", str(state), "--name", "m", "--pace", "fast", "--mvv", str(mvv)],
                   check=True, capture_output=True)
    if approve:
        subprocess.run([PY, str(MISSION_STATE), "gate", str(state), "MVV", "--what", "MVV を承認"], check=True,
                       capture_output=True)
    return state


def load(path) -> dict:
    return json.loads(Path(path).read_text())


def steps_of(plan: dict) -> dict:
    return {s["id"]: s for s in plan["steps"]}


def assert_transitions_exist(plan: dict) -> None:
    ids = steps_of(plan)
    for s in plan["steps"]:
        for k in ("next", "on_fail", "skip_to", "gate_next"):
            if k in s:
                assert s[k] in ids or s[k] == "end", (plan.get("フェーズ"), s)


# ---------- new mission --pace fast ----------


def new_fast_mission(tmp_path, state, *extra):
    out = tmp_path / "m"
    p = cli("new", "mission", "--name", "m26", "--worktree", str(tmp_path), "--issue", "11", "12", "--design", "11",
            "--version", "10.18.0-dev.1", "--pace", "fast", "--state", str(state), "--out", str(out), *extra)
    return p, out


def test_fast_mission_puts_check_then_dev_then_prod_after_the_implementation(tmp_path):
    p, out = new_fast_mission(tmp_path, mission_state(tmp_path))
    assert p.returncode == 0, p.stdout + p.stderr
    manifest = load(out / "mission.json")
    assert manifest["進め方"] == "fast" and manifest["状態"].endswith("mission-state.json")
    waves = {w["name"]: w for w in manifest["波"]}
    assert [w["name"] for w in manifest["波"]] == ["設計", "関門 1", "実装", "検査", "開発版", "本番"]
    cmd = waves["実装"]["command"]
    check, dev, prod = waves["検査"]["plans"][0], waves["開発版"]["plans"][0], waves["本番"]["plans"][0]
    assert cmd.index("--then " + check) < cmd.index("--then " + dev) < cmd.index("--then " + prod)
    assert "mission/" not in json.dumps(manifest, ensure_ascii=False)

    impl = load(waves["実装"]["plans"][0])
    assert impl["起点"] == "origin/develop" and next(s for s in impl["steps"] if s["type"] == "pr")["base"] == "develop"
    c = load(check)
    assert c["実行の条件"]["skip_code"] == 3 and "check-trigger.py eval --id m26-1" in c["実行の条件"]["cmd"]
    assert steps_of(load(dev))["facts"]["gate_as_ok"] is True
    first = load(prod)["steps"][0]
    assert first["id"] == "mvv" and "mvv-gate.py check" in first["cmd"] and "--gate release" in first["cmd"]
    assert first["gate_next"] == "end"
    design = load(waves["設計"]["plans"][0])
    ds = steps_of(design)
    assert "--gate design" in ds["mvv"]["cmd"] and ds["mvv"]["next"] == "approve" and ds["mvv"]["gate_next"] == "end"
    assert "design-approved" in ds["approve"]["cmd"] and ds["approve"]["next"] == "merge"
    for path in [*waves["設計"]["plans"], *waves["実装"]["plans"], check, dev, prod]:
        assert_transitions_exist(load(path))


@pytest.mark.parametrize("change", ["unapproved", "changed"])
def test_fast_mission_is_refused_without_a_matching_approval(tmp_path, change):
    state = mission_state(tmp_path, approve=change != "unapproved")
    if change == "changed":
        mvv = Path(load(state)["mvv"]["path"])
        mvv.write_text(mvv.read_text() + "足した行\n")
    p, out = new_fast_mission(tmp_path, state)
    assert p.returncode == 1, p.stdout + p.stderr
    assert json.loads(p.stdout)["status"] == "stopped"
    assert not list(out.glob("*.json")) if out.exists() else True


def test_fast_mission_is_refused_for_operation_mode(tmp_path):
    p, out = new_fast_mission(tmp_path, mission_state(tmp_path), "--mode", "operation")
    assert p.returncode == 1 and "モード" in json.loads(p.stdout)["summary"]


def test_fast_mission_is_refused_without_the_declaration(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".ndf").mkdir(parents=True)
    (repo / ".ndf" / "worktree.json").write_text('{"version": 1, "base_branch": "develop", "production_branch": "main"}')
    (repo / ".ndf" / "supervise.json").write_text((REPO / ".ndf" / "supervise.json").read_text())
    out = tmp_path / "m"
    p = cli("new", "mission", "--name", "m", "--worktree", str(repo), "--issue", "1", "--version", "1.0.0-dev.1",
            "--pace", "fast", "--state", str(mission_state(tmp_path)), "--out", str(out), cwd=repo)
    assert p.returncode == 1 and "pace.json" in json.loads(p.stdout)["summary"]


# ---------- new check --since-last ----------


def test_check_since_last_has_a_condition_and_every_failure_reaches_abort(tmp_path):
    out = tmp_path / "check.json"
    p = cli("new", "check", "--since-last", "--id", "m-3", "--worktree", str(tmp_path), "--out", str(out))
    assert p.returncode == 0, p.stdout + p.stderr
    plan = load(out)
    assert plan["branch"] == "check/m-3" and plan["実行の条件"]["skip_code"] == 3
    ids = [s["id"] for s in plan["steps"]]
    assert ids[:3] == ["prepare", "pr", "assess"]
    s = steps_of(plan)
    assert s["pr"]["base"] == "check-base/m-3" and s["pr"]["body"] == "template"
    assert "check-trigger.py scope --id m-3" in s["refactor"]["args"]
    assert s["finish"]["skip_to"] == "record" and s["record"]["next"] == "end"
    for step in plan["steps"]:
        if step["type"] == "run" and not step["id"].startswith("abort"):
            assert step.get("on_fail", "").startswith("abort") or step["id"] in ("assess", "test-all"), step
    assert "--failed" in s["abort"]["cmd"] and "--pr {pr}" in s["abort"]["cmd"]
    assert "--pr" not in s["abort-before-pr"]["cmd"]
    assert s["prepare"]["on_fail"] == "abort-before-pr" and s["ready"]["on_fail"] == "abort"
    assert_transitions_exist(plan)


def test_check_since_last_with_pr_is_a_usage_error(tmp_path):
    p = cli("new", "check", "--since-last", "--pr", "5", "--id", "m", "--worktree", str(tmp_path))
    assert p.returncode == 2


# ---------- new close ----------


def test_close_runs_spec_close_and_retro_once_each_in_order(tmp_path):
    out = tmp_path / "close"
    p = cli("new", "close", "--name", "m26", "--worktree", str(tmp_path), "--issue", "11", "12",
            "--version", "10.18.0-dev.2", "--prod", "10.18.0", "--state", str(mission_state(tmp_path)),
            "--out", str(out))
    assert p.returncode == 0, p.stdout + p.stderr
    manifest = load(out / "mission.json")
    waves = {w["name"]: w for w in manifest["波"]}
    assert [w["name"] for w in manifest["波"]] == ["最終の検査", "開発版", "本番", "まとめ"]
    final = load(waves["最終の検査"]["plans"][0])
    assert "--final" in final["実行の条件"]["cmd"]
    for name in ("開発版", "本番"):
        cond = load(waves[name]["plans"][0])["実行の条件"]
        assert "check-trigger.py changed --id m26-final" in cond["cmd"]
    close = load(waves["まとめ"]["plans"][0])
    stages = [s.get("stage") for s in close["steps"]]
    for stage in ("確定仕様化", "後片付け", "振り返り"):
        assert stages.count(stage) == 1
    assert stages.index("確定仕様化") < stages.index("後片付け") < stages.index("振り返り")
    cl = steps_of(close)["close"]["cmd"]
    assert "--record-pr {queue_pr:release-prod}" in cl and "--issues 11,12" in cl and "--with-verification" in cl
    assert close["課題"] == [11, 12] and close.get("記録")
    cmd = waves["最終の検査"]["command"]
    assert cmd.count("--then") == 3
    assert_transitions_exist(close)


# ---------- new release --mvv・new impl --escape-of ----------


def test_release_with_mvv(tmp_path):
    state = mission_state(tmp_path)
    for channel, version in (("prod", "10.18.0"), ("dev", "10.18.0-dev.1")):
        out = tmp_path / f"{channel}.json"
        p = cli("new", "release", "--version", version, "--channel", channel, "--prs", "5", "--mvv", str(state),
                "--worktree", f"/r/.worktrees/release/v{version}", "--out", str(out))
        assert p.returncode == 0, p.stderr
        plan = load(out)
        if channel == "prod":
            assert plan["steps"][0]["id"] == "mvv" and "MVV の判定" in plan["規則"]
        else:
            assert steps_of(plan)["facts"]["gate_as_ok"] is True


def test_impl_escape_of_records_after_the_merge(tmp_path):
    out = tmp_path / "impl.json"
    p = cli("new", "impl", "--issue", "7", "--worktree", str(tmp_path), "--tests", "x", "--title", "t",
            "--escape-of", "12", "--out", str(out))
    assert p.returncode == 0, p.stderr
    s = steps_of(load(out))
    assert s["merge"]["next"] == "escape" and "escape --pr {pr} --of 12" in s["escape"]["cmd"]
    assert s["escape"]["next"] == "end"


# ---------- 実行: 実行の条件・--then の段・{queue_pr:<名>}・gate_as_ok ----------


def plan_file(tmp_path, name, steps, **extra) -> str:
    f = tmp_path / f"{name}.json"
    f.write_text(json.dumps({"フェーズ": "試験", "課題": [], "作業場所": str(tmp_path), "steps": steps, **extra},
                            ensure_ascii=False))
    return str(f)


def test_condition_skip_finishes_without_a_worktree_and_the_next_stage_runs(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    wt = repo / ".worktrees" / "check" / "x"
    cond = plan_file(tmp_path, "cond", [{"id": "t", "type": "run", "cmd": f"touch {tmp_path}/ran", "next": "end"}],
                     **{"作業場所": str(wt), "branch": "check/x", "起点": "HEAD", "リポジトリ": str(repo),
                        "実行の条件": {"cmd": "echo '{\"summary\": \"立たない\"}'; exit 3", "skip_code": 3}})
    after = plan_file(tmp_path, "after", [{"id": "t", "type": "run", "cmd": f"touch {tmp_path}/after", "next": "end"}])
    res = sv.cmd_queue([cond], 3, poll=0.05, then=[[after]])
    assert [i["result"] for i in res["items"]] == ["完了", "完了"], res
    assert not wt.exists() and not (tmp_path / "ran").exists() and (tmp_path / "after").exists()
    report = (sv.state_dir_of(cond) / "report.md").read_text()
    assert "- 結果: 完了" in report and "実行の条件に当たらない（立たない）" in report


def test_condition_zero_runs_and_other_codes_stop(tmp_path):
    ok = plan_file(tmp_path, "ok", [{"id": "t", "type": "run", "cmd": f"touch {tmp_path}/ran", "next": "end"}],
                   **{"実行の条件": {"cmd": "true", "skip_code": 3}})
    bad = plan_file(tmp_path, "bad", [{"id": "t", "type": "run", "cmd": "true", "next": "end"}],
                    **{"実行の条件": {"cmd": "exit 2", "skip_code": 3}})
    res = sv.cmd_queue([ok, bad], 3, poll=0.05)
    got = {Path(i["plan"]).stem: i["result"] for i in res["items"]}
    assert got == {"ok": "完了", "bad": "止まった"} and (tmp_path / "ran").exists()


def test_then_stages_run_in_order_and_stop_after_a_failed_stage(tmp_path):
    a = plan_file(tmp_path, "a", [{"id": "t", "type": "run", "cmd": f"date +%s.%N > {tmp_path}/a", "next": "end"}])
    b = plan_file(tmp_path, "b", [{"id": "t", "type": "run", "cmd": f"sleep 0.2; date +%s.%N > {tmp_path}/b",
                                   "next": "end"}])
    c = plan_file(tmp_path, "c", [{"id": "t", "type": "run", "cmd": f"date +%s.%N > {tmp_path}/c", "next": "end"}])
    res = sv.cmd_queue([a], 3, poll=0.05, then=[[b], [c]])
    assert [i["result"] for i in res["items"]] == ["完了"] * 3
    t = {k: float((tmp_path / k).read_text()) for k in "abc"}
    assert t["a"] <= t["b"] <= t["c"]
    fail = plan_file(tmp_path, "fail", [{"id": "t", "type": "run", "cmd": "exit 1"}])
    res = sv.cmd_queue([a], 3, poll=0.05, then=[[fail], [c]])
    assert [i["result"] for i in res["items"]] == ["完了", "止まった", sv.NOT_RUN]


def report_with_pr(plan: str, pr: str) -> None:
    d = sv.state_dir_of(plan)
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text(f"## フェーズの報告\n\n- 結果: 完了\n- Pull Request: {pr}\n")


def test_queue_pr_placeholder_takes_the_named_plan_or_zero(tmp_path, monkeypatch):
    prod = plan_file(tmp_path, "3-release-prod", [{"id": "t", "type": "run", "cmd": "true", "next": "end"}])
    impl = plan_file(tmp_path, "1-impl-5", [{"id": "t", "type": "run", "cmd": "true", "next": "end"}])
    close = plan_file(tmp_path, "4-close", [{"id": "t", "type": "run", "cmd": "echo {queue_pr:release-prod}",
                                             "next": "end"}])
    seen = {}

    def batch(plans, m, poll):
        items = []
        for p in plans:
            if p == close:
                seen["cmd"] = load(close)["steps"][0]["cmd"]
                items.append({"plan": p, "result": "完了", "report": ""})
            else:
                report_with_pr(p, "https://github.com/o/r/pull/77" if p == prod else "78")
                items.append({"plan": p, "result": "完了", "report": str(sv.state_dir_of(p) / "report.md")})
        return items
    monkeypatch.setattr(sv, "run_batch", batch)
    sv.cmd_queue([impl], 3, poll=0.05, then=[[prod], [close]])
    assert seen["cmd"] == "echo 77"
    close2 = plan_file(tmp_path, "4-close-b", [{"id": "t", "type": "run", "cmd": "echo {queue_pr:release-prod}",
                                                "next": "end"}])
    (sv.state_dir_of(prod) / "report.md").write_text("## フェーズの報告\n\n- 結果: 完了\n- Pull Request: 無し\n")
    assert sv.fill_queue_pr(close2, [{"plan": prod, "result": "完了",
                                      "report": str(sv.state_dir_of(prod) / "report.md")}]) is None
    assert load(close2)["steps"][0]["cmd"] == "echo 0"


def test_queue_pr_placeholder_reads_a_sibling_plan_when_queued_alone(tmp_path):
    prod = plan_file(tmp_path, "3-release-prod", [])
    report_with_pr(prod, "91")
    close = plan_file(tmp_path, "4-close", [{"id": "t", "type": "run", "cmd": "echo {queue_pr:release-prod}"}])
    assert sv.fill_queue_pr(close, []) is None
    assert load(close)["steps"][0]["cmd"] == "echo 91"


def test_gate_as_ok_copies_the_presentation_and_goes_on(tmp_path):
    pres = tmp_path / "facts.md"
    pres.write_text("# 提示物\n")
    out = json.dumps({"tool": "t", "status": "gate", "summary": "s", "items": [], "metrics": {},
                      "presentation_path": str(pres)})
    s = sv.Supervisor({"フェーズ": "試験", "課題": [], "作業場所": str(tmp_path), "steps": [
        {"id": "facts", "type": "run", "cmd": f"echo '{out}'; exit 10", "gate_as_ok": True,
         "presentation_to": "issues/a.md", "next": "after"},
        {"id": "after", "type": "run", "cmd": "true", "next": "end"}]}, tmp_path / "state")
    text = s.run()
    assert "- 結果: 完了" in text and "after" in s.results
    assert (tmp_path / "issues" / "a.md").read_text() == "# 提示物\n"
    assert f"提示物: {tmp_path / 'issues' / 'a.md'}" in text


def test_mvv_step_returning_ten_makes_the_queue_a_gate(tmp_path):
    prod = plan_file(tmp_path, "prod", [
        {"id": "mvv", "type": "run", "cmd": "exit 10", "next": "bump", "gate_next": "end"},
        {"id": "bump", "type": "run", "cmd": f"touch {tmp_path}/bumped", "next": "end"}])
    res = sv.cmd_queue([prod], 3, poll=0.05)
    assert res["status"] == "gate" and res["items"][0]["result"] == "関門"
    assert not (tmp_path / "bumped").exists()


def test_a_fast_plan_records_the_pace_before_the_first_stage(tmp_path):
    log = tmp_path / "rec.log"
    rec = tmp_path / "rec.sh"
    rec.write_text(f'echo "$@" >> {log}\n')
    s = sv.Supervisor({"フェーズ": "試験", "課題": [5, 6], "作業場所": str(tmp_path), "記録": str(rec), "進め方": "fast",
                       "steps": [{"id": "a", "type": "run", "cmd": "true", "stage": "確定仕様化", "next": "b"},
                                 {"id": "b", "type": "run", "cmd": "true", "stage": "振り返り", "next": "end"}]},
                      tmp_path / "state")
    s.run()
    assert log.read_text().splitlines() == ["5 pace fast", "5 stage 確定仕様化", "6 pace fast", "6 stage 確定仕様化",
                                            "5 stage 振り返り", "6 stage 振り返り"]
