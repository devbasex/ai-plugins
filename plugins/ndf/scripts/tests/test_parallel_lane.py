"""レーンの判定（#2436 の I12）と、レーンの `queue --max` の既定（プランの本数）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import parallel_lane as pl  # noqa: E402
import parallel_plan as pp  # noqa: E402

PLAN = {
    "version": 1,
    "name": "p1",
    "rules": [],
    "lanes": [{"name": "la", "input": {"kind": "sprint", "issues": [1]}, "plans": 2, "touches": ["a/"]}],
}


def _main(tmp_path: Path) -> Path:
    d = pp.plan_dir(tmp_path, "p1")
    d.mkdir(parents=True)
    (d / "plan.json").write_text(json.dumps(PLAN))
    return tmp_path


def test_lane_only_while_plan_is_running(tmp_path):
    main = _main(tmp_path)
    running = lambda _m, _n: "PENDING"  # noqa: E731
    assert pl.lane_of(main, "la", status=running)[1].plans == 2
    assert pl.lane_of(main, "lb", status=running) is None
    assert pl.lane_of(main, "la", status=lambda _m, _n: "SUCCESS") is None
    assert pl.lane_of(main, "la", status=lambda _m, _n: None) is None


def test_queue_max_default(tmp_path, monkeypatch):
    import supervise

    main = _main(tmp_path)
    monkeypatch.setattr(pl, "lane_plans", lambda m, lane=None: 2 if Path(m) == main else None)
    assert supervise.default_max(str(main)) == 2
    assert supervise.default_max(str(tmp_path / "other")) == 3


def test_generated_queue_lines_use_lane_plans(tmp_path, monkeypatch):
    """スプリントのステージとループの 1 回のまとめが組む queue の 1 行も、レーンのプランの本数で流す。"""
    import sprint_loop_gate as gate
    from supervise_lib import sprint

    monkeypatch.setattr(pl, "lane_plans", lambda m, lane=None: 1)
    wave = {"name": "実装", "plans": {"a": {"name": "a", "steps": []}}}
    assert " --max 1 --done " in sprint._stage_entry(wave, 1, tmp_path)["command"]
    assert " --max 1 --done " in gate.queue_command([["a.json"]], tmp_path / "done.json")


def test_non_lane_sprint_keeps_prod_plan(monkeypatch):
    from supervise_lib import sprint

    monkeypatch.setattr(sprint, "parallel_lane", lambda repo: None)
    assert list(sprint.prod_plans(None, "/r", lambda: {"x": 1})) == ["release-prod"]
    monkeypatch.setattr(sprint, "parallel_lane", lambda repo: ("p1", "la"))
    import parallel_release

    monkeypatch.setattr(
        parallel_release,
        "plan_release_request",
        lambda a, repo, plan, lane, prs=None: {"フェーズ": "リリースの依頼", "plan": plan, "lane": lane},
    )
    got = sprint.prod_plans(None, "/r", lambda: {"x": 1})
    assert got == {"release-request": {"フェーズ": "リリースの依頼", "plan": "p1", "lane": "la"}}


def test_selected_lane_builds_its_own_prod_plan(monkeypatch):
    """選んで集める取り込み方（#3121）はまとめたリリースを組まないため、レーンでも自分の本番のプランを組む。
    sprint_plans のリリース承認ゲートの分岐も同じ判定（release_lane）を読む。"""
    import argparse

    from supervise_lib import sprint

    monkeypatch.setattr(sprint, "parallel_lane", lambda repo: ("p1", "la"))
    flow = {"start": "production", "integrate": "check", "doc_prs": "sprint", "promotion": "selected"}
    a = argparse.Namespace(flow=flow, base="develop", production_branch="main")
    assert sprint.release_lane(a, "/r") is None
    assert list(sprint.prod_plans(a, "/r", lambda: {"x": 1})) == ["release-prod"]
    whole = argparse.Namespace(flow=None, base="develop", production_branch="main")
    assert sprint.release_lane(whole, "/r") == ("p1", "la")


def test_release_request_plan_has_one_gate_step():
    import argparse

    import supervise_lib  # noqa: F401  lib/ と supervise_lib を読めるようにする
    import parallel_release

    plan = parallel_release.plan_release_request(argparse.Namespace(issue=[2436], mode="standard"), "/r", "p1", "la", ["{queue_pr:check}"])
    assert plan["フェーズ"] == "リリースの依頼" and [s["id"] for s in plan["steps"]] == ["parallel-request"]
    # 課題の番号はプランの `課題` だけに持ち、コマンドは流す時点に埋める差し込みを持つ（#2717 の I3）
    assert plan["課題"] == [2436] and "request p1 --lane la --prs {queue_pr:check} --issues {plan_issues} " in plan["steps"][0]["cmd"]


def test_present_gate_puts_approve_after_mvv(tmp_path):
    import supervise_lib  # noqa: F401
    import parallel_release

    steps = parallel_release.present_gate_steps(str(tmp_path / "a.json"), "/r", "issues/x.md", "1 2", "standard", "/f", "/a")
    ids = [s["id"] for s in steps]
    assert ids[:3] == ["mvv", "note", "approve"] and steps[1]["next"] == "approve" and steps[2]["next"] == "end"


def test_lane_request_without_prs_uses_previous_stage_prs(monkeypatch):
    from supervise_lib import sprint

    import parallel_release

    seen = {}
    monkeypatch.setattr(sprint, "parallel_lane", lambda repo: ("p1", "la"))
    monkeypatch.setattr(parallel_release, "plan_release_request", lambda a, repo, plan, lane, prs=None: seen.setdefault("prs", prs))
    sprint.prod_plans(None, "/r", lambda: {})
    assert seen["prs"] == ["{queue_prs}"]


def test_closes_by_pr_reads_in_bulk(tmp_path):
    import supervise_lib  # noqa: F401
    import parallel_release

    calls = []

    def bulk(prs, cwd=None):
        calls.append(list(prs))
        return {1: {"closingIssuesReferences": [{"number": 7}]}, 2: None}, ""

    def per_pr(*a, **k):
        raise AssertionError("PR ごとに gh を起動しない")

    assert parallel_release.closes_by_pr(tmp_path, [1, 2], run=per_pr, bulk=bulk) == {1: [7], 2: []}
    assert calls == [[1, 2]]


def test_present_plan_starts_and_collects_range_only(tmp_path):
    import supervise_lib  # noqa: F401
    import parallel_release
    from supervise_lib import limits

    d = tmp_path / "releases" / "1" / "1"
    present, _ = parallel_release.plan_parallel_release(SCRIPTS.parents[2], "p1", d, "1.2.3")
    by = {s["id"]: s for s in present["steps"]}
    assert limits.run_error(present) is None  # on_fail が無いステップを指さない
    assert not any(by[i].get("on_fail") for i in ("facts", "explain", "others"))
    assert by["facts"].get("gate_as_ok") is True  # facts の 10 を承認ゲートで残さない
    assert "--prs 0" not in json.dumps(present) and "--prs  --approval" in by["facts"]["cmd"]


def test_release_plan_keeps_base_branch_and_takes_approved_commit_for_worktree(tmp_path):
    import supervise_lib  # noqa: F401
    import parallel_release
    from supervise_lib import paths

    d = tmp_path / "releases" / "1" / "1"
    _, release = parallel_release.plan_parallel_release(SCRIPTS.parents[2], "p1", d, "1.2.3")
    assert release["起点"].startswith("origin/") and release["起点のコミット"] == "{queue_approved:present}"
    plan = {**release, "起点のコミット": "abc123"}
    assert paths._resolve_base(plan, str(tmp_path)) == ("abc123", None)  # 作業ツリーは承認したコミットから


def test_answer_state_carries_approved_project_mvv(tmp_path, monkeypatch):
    import parallel_release
    import project_mvv

    approved = project_mvv.ProjectMvv(status="approved", version=4, sha256="s")
    monkeypatch.setattr(project_mvv, "load_mvv", lambda root: approved)
    assert parallel_release.answer_state(tmp_path) == {"gates": [], "project_mvv": {"version": 4, "sha256": "s"}}
    monkeypatch.setattr(project_mvv, "load_mvv", lambda root: project_mvv.ProjectMvv(status="none"))
    assert parallel_release.answer_state(tmp_path) == {"gates": []}


def test_present_writes_to_present_plan_state_dir(tmp_path, monkeypatch):
    import supervise_lib  # noqa: F401
    import approved_commit
    import parallel_release
    from release_lib import included
    from supervise_lib.paths import state_dir_of

    monkeypatch.setattr(approved_commit, "material_sha", lambda p: "abc")
    monkeypatch.setattr(included, "read_included", lambda p: [5])
    plan = pp.ParallelPlan.model_validate(PLAN, context={"max_plans": 10, "max_named": 10})
    d = tmp_path / "releases" / "1" / "1"
    parallel_release.present(tmp_path, plan, tmp_path / "a.md", [], d, closes_of=lambda r, prs: {})
    work = state_dir_of(str(d / "present.json")) / "work"
    assert (work / "prs.txt").read_text().strip() == "5" and (work / "approved.txt").read_text().strip() == "abc"


def test_lane_prod_stage_chains_after_release(monkeypatch, tmp_path):
    import argparse

    from supervise_lib import sprint

    monkeypatch.setattr(sprint, "parallel_lane", lambda repo: ("p1", "la"))
    monkeypatch.setattr(sprint, "has_release_template", lambda a: True)
    monkeypatch.setattr(sprint, "has_prod_branch", lambda a: True)
    monkeypatch.setattr(sprint, "design_read", lambda a: False)
    monkeypatch.setattr(sprint, "advises", lambda a: False)
    for f in ("plan_sprint_branch", "sprint_impl_plans", "plan_sprint_check", "plan_sprint_release"):
        monkeypatch.setattr(sprint, f, lambda *a, **k: {})
    monkeypatch.setattr(sprint, "prod_plans", lambda a, repo, build, prs=None: {"release-request": {}})
    a = argparse.Namespace(worktree=str(tmp_path), design=[], issue=[1], version=None, pace="normal")
    waves = sprint.sprint_plans(a)
    assert waves[-1]["name"] == "本番" and waves[-1]["then_of"] == waves[0]["name"]  # 先頭の command へ --then でつなぐ


def test_merged_on_production_checks_the_production_branch(tmp_path, monkeypatch):
    """リリース済みの照合は origin/main 固定でなく、本番チャネル（repo.production_branch）の先を見る。"""
    import subprocess

    import repo

    monkeypatch.setattr(repo, "production_branch", lambda _root, decl=None: ("live", "宣言"))
    seen = []

    def run(cmd, **_kw):
        seen.append(cmd)
        out = "abc\n" if cmd[:2] == ["gh", "pr"] else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    assert pl.merged_on_production(tmp_path, [7], run=run) is True
    assert seen[-1][-1] == "origin/live"
    monkeypatch.setattr(repo, "production_branch", lambda _root, decl=None: (None, "無い"))
    assert pl.merged_on_production(tmp_path, [7], run=run) is False


def _review_out(d: Path, rounds: int) -> None:
    d.mkdir(parents=True, exist_ok=True)
    got = {"tool": "cross-review-drive", "status": "ok", "items": [], "metrics": {"rounds": rounds}}
    (d / "05-review.out").write_text("ログ\n" + json.dumps(got) + "\n")


def test_present_writes_check_results_of_each_lane(tmp_path, monkeypatch):
    """まとめたリリースの承認資料の `## 検査の結果` に、レーンの依頼のスプリントの検査のプランの結果を載せ、
    検査の状態の無い Pull Request は番号で挙げる（#3585）。"""
    import supervise_lib  # noqa: F401
    import approved_commit
    import parallel_release
    from release_lib import included

    sprint = tmp_path / "work" / "sprint-la"
    _review_out(sprint / "3-check-state", 1)
    _review_out(sprint / "7-check-state", 2)  # 連番の大きい検査のプランを読む
    req = {"id": "la-r1", "lane": "la", "prs": [5], "sprint_state": str(sprint / "9-release-request-state")}
    approval = tmp_path / "a.md"
    approval.write_text(
        "# 承認\n\n## 検査の結果\n\n- 無し（レビュー・リファクタリング・全体テストの結果が検査の状態に無い）\n\n## 同意を求めること\n\n- x\n"
    )
    monkeypatch.setattr(approved_commit, "material_sha", lambda p: "abc")
    monkeypatch.setattr(included, "read_included", lambda p: [5, 6])
    plan = pp.ParallelPlan.model_validate(PLAN, context={"max_plans": 10, "max_named": 10})
    parallel_release.present(tmp_path, plan, approval, [req], tmp_path / "releases" / "1" / "1", closes_of=lambda r, prs: {})
    text = approval.read_text()
    section = text.split("## 検査の結果", 1)[1].split("## 同意を求めること", 1)[0]
    assert "無し（" not in section
    assert "レーン la（#5。" in section and "cross-review 2 ラウンド" in section
    assert "検査の状態が無い Pull Request: #6" in section
    assert text.endswith("## 同意を求めること\n\n- x\n")


def test_check_md_names_every_pr_without_a_check_state(tmp_path):
    import parallel_present

    mapped = {"lanes": {"la": {"prs": [5], "requested": False, "requests": []}}, "orphans": [7]}
    assert parallel_present.check_md(tmp_path, mapped, []) == ["- 検査の状態が無い Pull Request: #5 #7"]


def test_check_md_splits_requests_of_one_lane_by_their_own_check_state(tmp_path):
    """同じレーンに 2 スプリント分の依頼があれば、依頼ごとにその依頼より前の検査の結果を、その依頼の PR に付ける（#3585）。"""
    import parallel_present

    sprint = tmp_path / "work" / "sprint-la"
    _review_out(sprint / "3-check-state", 1)
    _review_out(sprint / "7-check-state", 2)
    reqs = [
        {"id": "r1", "lane": "la", "prs": [5], "sprint_state": str(sprint / "5-release-request-state")},
        {"id": "r2", "lane": "la", "prs": [6], "sprint_state": str(sprint / "9-release-request-state")},
    ]
    mapped = {"lanes": {"la": {"prs": [5, 6], "requested": True, "requests": ["r1", "r2"]}}, "orphans": []}
    got = parallel_present.check_md(tmp_path, mapped, reqs)
    heads = [x for x in got if x.startswith("- レーン")]
    assert heads == [
        f"- レーン la（#5。検査の状態 `{sprint / '3-check-state'}`）",
        f"- レーン la（#6。検査の状態 `{sprint / '7-check-state'}`）",
    ]


def test_production_plan_has_no_fix_step_after_the_approval(tmp_path):
    """本番のプランは承認の後にコードを直すステップ（fix）を持たず、judge はやり直すか止めるかだけを選ぶ（#3572 の今の NDF の側）。"""
    import parallel_release

    d = tmp_path / "releases" / "1" / "1"
    _, release = parallel_release.plan_parallel_release(SCRIPTS.parents[2], "p1", d, "1.2.3")
    steps = {s["id"]: s for s in release["steps"]}
    assert not [s for s in release["steps"] if s.get("type") == "work" and s.get("kind") == "修正"]
    assert "fix" not in steps and "fix" not in steps["judge"]["choices"] and "stop" in steps["judge"]["choices"]
    assert all(s.get("on_fail") != "fix" and s.get("next") != "fix" for s in release["steps"])


# ---- #3597: 依頼のコマンドは送らなかった依頼を送ったと返さない（受け入れ条件 3・12、決定 5） ----


def _cli(monkeypatch, tmp_path):
    import importlib.util

    import parallel_ledger as plg

    spec = importlib.util.spec_from_file_location("parallel_cli_request", SCRIPTS / "parallel.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "main_of", lambda: tmp_path)
    monkeypatch.setattr(pl, "run_status", lambda main, name: next(iter(pl.RUNNING)))
    return mod, plg


def _run(mod, capsys, *argv):
    import pytest

    with pytest.raises(SystemExit) as e:
        mod.main(list(argv))
    return e.value.code or 0, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_request_gate2_needs_either_report_or_confirmed_done(monkeypatch, tmp_path, capsys):
    mod, _ = _cli(monkeypatch, tmp_path)
    assert _run(mod, capsys, "request-gate2", "p1", "1", "1")[0] == 1
    assert _run(mod, capsys, "request-gate2", "p1", "1", "1", "--report", "r", "--confirmed", "--done", "d")[0] == 1
    assert _run(mod, capsys, "request-gate2", "p1", "1", "1", "--confirmed")[0] == 1


def test_request_gate2_does_not_say_sent_for_a_different_request_with_the_same_id(monkeypatch, tmp_path, capsys):
    """受け入れ条件 3: 同じ id で中身の違う依頼は 0 でなく、既にある id と理由を返す。中身が同じ再送は送り済みで 0。"""
    import parallel_gate2 as pgate

    mod, plg = _cli(monkeypatch, tmp_path)
    monkeypatch.setattr(pgate, "request_gate2", lambda *a: plg.Sent("p1-p1-1", written=False, same=False, existing="p1-p1-1"))
    code, out = _run(mod, capsys, "request-gate2", "p1", "17", "1", "--report", "r.md")
    assert code == 1 and out["status"] == "stopped" and out["metrics"]["existing"] == "p1-p1-1" and out["metrics"]["reason"]
    monkeypatch.setattr(pgate, "request_gate2", lambda *a: plg.Sent("p1-p1-1", written=False, same=True, existing="p1-p1-1"))
    code, out = _run(mod, capsys, "request-gate2", "p1", "17", "1", "--report", "r.md")
    assert code == 0 and out["metrics"]["already"] is True and "送り済み" in out["summary"]


def test_request_gate2_confirmed_refuses_without_a_confirm_and_an_unreadable_done(monkeypatch, tmp_path, capsys):
    import parallel_gate2 as pgate

    mod, _ = _cli(monkeypatch, tmp_path)
    done = tmp_path / "done.json"
    assert _run(mod, capsys, "request-gate2", "p1", "1", "1", "--confirmed", "--done", str(done))[0] == 2
    done.write_text("{}")

    def refuse(*a):
        raise pgate.NotConfirmed("最後の起動が確定でない（送らない）")

    monkeypatch.setattr(pgate, "request_confirmed", refuse)
    code, out = _run(mod, capsys, "request-gate2", "p1", "1", "1", "--confirmed", "--done", str(done))
    assert code == 4 and "確定でない" in out["summary"]


def test_request_refuses_a_different_request_and_keeps_the_record_unwritten(monkeypatch, tmp_path, capsys):
    """受け入れ条件 3・12: 中身の違う同じ id の依頼は 1 で依頼の記録を書かない。答えのある同じ依頼の再送は 10 と送り済み。"""
    mod, plg = _cli(monkeypatch, tmp_path)
    monkeypatch.setattr(pl, "merged_on_production", lambda main, prs: False)
    state = tmp_path / "st"
    argv = ("request", "p1", "--lane", "la", "--prs", "10", "--state", str(state))
    monkeypatch.setattr(pl, "send_release_request", lambda *a: plg.Sent("la-r1", written=False, same=False, existing="la-r1"))
    code, out = _run(mod, capsys, *argv)
    assert code == 1 and out["metrics"]["existing"] == "la-r1" and not (state / "work" / "request.txt").exists()
    monkeypatch.setattr(pl, "send_release_request", lambda *a: plg.Sent("la-r1", written=False, same=True, answered=True, existing="la-r1"))
    code, out = _run(mod, capsys, *argv)
    assert code == 10 and out["metrics"]["already"] is True and out["metrics"]["answered"] is True
    assert json.loads((state / "work" / "request.txt").read_text())["id"] == "la-r1"


def test_deliver_message_does_not_notify_a_different_request(tmp_path, monkeypatch):
    """決定 5: 同じキーで中身が違えば台帳へ書かず、耐久ワークフローへも知らせない。中身が同じなら知らせる。"""
    seen = []
    monkeypatch.setattr(pl, "record_exists", lambda main, name: seen.append(name) or False)
    msg = {"kind": "ask", "id": "la-q1", "lane": "la", "question": "消す？", "at": "t1"}
    assert pl.deliver_message(tmp_path, "p1", msg, "inbox", "la-q1").written
    got = pl.deliver_message(tmp_path, "p1", {**msg, "question": "別の問い"}, "inbox", "la-q1")
    assert (got.written, got.same, got.existing) == (False, False, "la-q1") and len(seen) == 1
    got = pl.deliver_message(tmp_path, "p1", {**msg, "at": "t2"}, "inbox", "la-q1")
    assert (got.written, got.same) == (False, True) and len(seen) == 2
