"""`ndf issues` の副命令（候補・順位・反映・戻し・深さ）と状態の置き場（#1640・#2064・#2423・#2540・#2701・#1595）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import cli as top
from ndf.domains.backlog import cli, depth, state
from ndf.domains.backlog.model import FORWARD


def _sd(root: Path) -> Path:
    return root / ".ndf" / "backlog" / "state" / "acme__widgets"


def test_issues_is_a_subcommand_of_ndf():
    assert top.COMMANDS["issues"] == cli.__name__
    assert cli.TOOL == "issues"


def test_state_dir_is_in_the_main_directory_from_any_worktree(repo_dir, git_in, tmp_path, monkeypatch):
    # #1640・#1595 の関連 1: worktree から打ってもメインディレクトリの .ndf/backlog/state/<repo> を返す
    wt = tmp_path / "wt"
    git_in(repo_dir, "worktree", "add", "-q", "-b", "feat", str(wt))
    assert state.state_dir(wt, "acme/widgets") == _sd(repo_dir)
    monkeypatch.setenv("NDF_UPKEEP_STATE_DIR", str(tmp_path / "env"))
    assert state.state_dir(wt, "acme/widgets") == tmp_path / "env" / "acme__widgets"


def test_state_dir_stops_outside_git(tmp_path):
    with pytest.raises(state.StateError):
        state.state_dir(tmp_path, "acme/widgets")


def test_broken_previous_candidates_stop_instead_of_reading_empty(repo_dir, fake, run_issues):
    _sd(repo_dir).mkdir(parents=True)
    (_sd(repo_dir) / "candidates.json").write_text("{broken")
    got = run_issues("candidates", "--repo", "acme/widgets")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid"


def test_deferred_candidates_are_picked_up_by_the_next_run(repo_dir, fake, run_issues):
    # #1640: 上限を超えて持ち越した候補が、次の回の candidates で carried として拾われる（--add で渡さない）
    for n in (1, 2, 3):
        fake.issue(n)
    first = run_issues("candidates", "--repo", "acme/widgets", "--limit", "1")
    assert first["status"] == "ok" and first["metrics"]["deferred"] == [2, 3]
    fake.issue(4)
    second = run_issues("candidates", "--repo", "acme/widgets", "--limit", "2")
    assert [i["number"] for i in second["items"]] == [2, 3]
    assert all("carried" in i["routes"] for i in second["items"])


def test_added_issues_are_kept_beyond_the_limit(repo_dir, fake, run_issues):
    # #2064: --add で渡した課題は上限で切られない
    for n in range(1, 8):
        fake.issue(n)
    got = run_issues("candidates", "--repo", "acme/widgets", "--limit", "2", "--add", "5,6,7")
    assert [i["number"] for i in got["items"]] == [5, 6, 7]
    assert got["metrics"]["notes"]


def test_depth_limits_read_the_declaration(repo_dir, run_issues):
    # #2423: 宣言の depths.review.seconds 900 で 900、無ければ既定、形が違えば止まる
    assert run_issues("depth-limits", "review")["metrics"]["seconds"] == 1800
    decl = repo_dir / ".ndf" / "backlog.json"
    decl.parent.mkdir()
    decl.write_text(json.dumps({"depths": {"review": {"seconds": 900}}}))
    assert run_issues("depth-limits", "review")["metrics"]["seconds"] == 900
    decl.write_text(json.dumps({"depths": {"review": {"seconds": "900"}}}))
    got = run_issues("depth-limits", "review")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid"


def test_timed_out_depth_leaves_its_rest_to_the_next_candidates(repo_dir, fake, run_issues):
    # #2423: 打ち切りの残りが depths.jsonl に残り、次の候補の収集が持ち越しとして拾う
    for n in (1, 2, 3):
        fake.issue(n)
    got = run_issues("depth-record", "--repo", "acme/widgets", "--depth", "review", "--event", "timed_out", "--left", "3")
    assert got["metrics"]["left"] == [3]
    assert depth.left_over(_sd(repo_dir)) == [3]
    cand = run_issues("candidates", "--repo", "acme/widgets", "--limit", "1")
    assert [i["number"] for i in cand["items"]] == [3]
    run_issues("depth-record", "--repo", "acme/widgets", "--depth", "review", "--event", "ran")
    assert depth.left_over(_sd(repo_dir)) == []


def _scores(path: Path, rows: list[dict]) -> str:
    path.write_text(json.dumps({"issues": rows}))
    return str(path)


def test_review_rank_asks_to_rescore_stale_estimates(repo_dir, fake, run_issues, tmp_path):
    # #2540: 見積の欠けた課題を残したまま見直しの順位を打つと、採点を求める形で返る
    fake.issue(1, milestone="01")
    fake.issue(2, milestone="01")
    sd = _sd(repo_dir)
    sd.mkdir(parents=True)
    (sd / "estimates.json").write_text(json.dumps({"2": {"number": 2, "ubv": 3, "tc": 1, "rr_oe": 1, "size": 1, "digest": "old"}}))
    cand = run_issues("candidates", "--repo", "acme/widgets")
    digest = {i["number"]: i["digest"] for i in cand["items"]}
    scores = _scores(tmp_path / "s.json", [{"number": 1, "ubv": 3, "tc": 1, "rr_oe": 1, "size": 1, "digest": digest[1]}])
    got = run_issues("rank", "--repo", "acme/widgets", "--scores", scores, "--depth", "review")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "incomplete"
    assert [g["name"] for g in got["gaps"]] == ["#2"]
    assert run_issues("rank", "--repo", "acme/widgets", "--scores", scores)["status"] == "ok"


def _ranked(repo_dir, fake, run_issues, tmp_path) -> dict:
    fake.issue(1, milestone="01 直近")
    fake.issue(2, milestone="01 直近")
    fake.issue(9, milestone="02 次")
    fake.milestones = {"01 直近": 1, "02 次": 2}
    rows = [{"number": n, "ubv": u, "tc": 1, "rr_oe": 1, "size": 1, "digest": f"d{n}"} for n, u in ((1, 2), (2, 1), (9, 20))]
    return run_issues("rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", rows), "--capacity", "2")


def test_apply_records_rejections_and_moves_approved(repo_dir, fake, run_issues, tmp_path):
    ranked = _ranked(repo_dir, fake, run_issues, tmp_path)
    assert [f["number"] for f in ranked["metrics"]["forward"]] == [9]
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"rank": ranked["metrics"]["digest"], "rejected": [{"number": 9, "direction": FORWARD}]}))
    got = run_issues("apply", "--repo", "acme/widgets", "--plan", str(plan))
    assert got["status"] == "ok"
    rejected = json.loads((_sd(repo_dir) / "rejected.json").read_text())
    assert rejected == [{"number": 9, "direction": FORWARD, "move_digest": ranked["metrics"]["forward"][0]["move_digest"]}]
    again = _ranked(repo_dir, fake, run_issues, tmp_path)
    assert again["metrics"]["forward"] == [] and again["metrics"]["rejected_stale"] == []


def test_apply_writes_nothing_when_the_plan_is_wrong(repo_dir, fake, run_issues, tmp_path):
    ranked = _ranked(repo_dir, fake, run_issues, tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"rank": ranked["metrics"]["digest"], "rejected": [{"number": 1, "direction": FORWARD}],
                                "reparent": [{"number": 1, "parent": 2}]}))  # fmt: skip
    got = run_issues("apply", "--repo", "acme/widgets", "--plan", str(plan))
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid"
    assert not (_sd(repo_dir) / "rejected.json").exists() and fake.writes == []


def test_reparent_with_an_issue_without_parent_and_revert(repo_dir, fake, run_issues, tmp_path):
    # #2701: 親の無い課題を含む付け直しが通り、前の状態に親なしで残り、戻すと親から外す
    fake.issue(10)
    fake.issue(11, parent=10)
    fake.issue(20)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"reparent": [{"number": 20, "parent": 10}, {"number": 11, "parent": None}]}))
    got = run_issues("apply", "--repo", "acme/widgets", "--plan", str(plan), "--run-id", "r1")
    assert got["status"] == "ok" and sorted(got["metrics"]["reparented"]) == [11, 20]
    before = json.loads((_sd(repo_dir) / "before-r1.json").read_text())
    assert {m["number"]: m["before"] for m in before["moves"]} == {20: None, 11: 10}
    assert fake.issues[20]["parent"] == 10 and fake.issues[11]["parent"] is None
    back = run_issues("revert", "--repo", "acme/widgets", "--run", "r1")
    assert back["status"] == "ok"
    assert fake.issues[20]["parent"] is None and fake.issues[11]["parent"] == 10


def test_revert_without_a_before_state_stops(repo_dir, fake, run_issues):
    got = run_issues("revert", "--repo", "acme/widgets", "--run", "nope")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid"


def test_reparent_rerun_keeps_the_first_before_state(repo_dir, fake, run_issues, tmp_path):
    # 同じ実行を打ち直しても、付け直した後の親で前の状態を上書きしない（戻すと元の親へ戻る）
    fake.issue(10)
    fake.issue(12)
    fake.issue(20, parent=12)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"reparent": [{"number": 20, "parent": 10}]}))
    for _ in range(2):
        assert run_issues("apply", "--repo", "acme/widgets", "--plan", str(plan), "--run-id", "r2")["status"] == "ok"
    before = json.loads((_sd(repo_dir) / "before-r2.json").read_text())
    assert before["moves"] == [{"number": 20, "before": 12, "after": 10}]
    assert run_issues("revert", "--repo", "acme/widgets", "--run", "r2")["status"] == "ok" and fake.issues[20]["parent"] == 12


def test_held_moves_return_a_gate_whose_route_keeps_the_state_dir(repo_dir, fake, run_issues, tmp_path):
    sd = tmp_path / "state"
    fake.issue(1, milestone="01 直近")
    fake.issue(2, milestone="01 直近")
    fake.issue(9, milestone="02 次")
    fake.milestones = {"01 直近": 1, "02 次": 2}
    rows = [{"number": n, "ubv": u, "tc": 1, "rr_oe": 1, "size": 1, "digest": f"d{n}"} for n, u in ((1, 2), (2, 1), (9, 20))]
    ranked = run_issues("rank", "--repo", "acme/widgets", "--state-dir", str(sd), "--scores", _scores(tmp_path / "s.json", rows),
                        "--capacity", "2")  # fmt: skip
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"rank": ranked["metrics"]["digest"], "reschedule": [{"number": 9, "direction": FORWARD, "milestone": "01 直近"}]})
    )
    got = run_issues("apply", "--repo", "acme/widgets", "--state-dir", str(sd), "--plan", str(plan))
    assert got["status"] == "gate" and got["metrics"]["held"] == [9] and fake.issues[9]["milestone"] == "02 次"
    argv = got["routes"]["approved"]["argv"]
    assert argv[argv.index("--state-dir") + 1] == str(sd)


def test_rescoring_without_a_dependency_drops_the_stored_edge(repo_dir, fake, run_issues, tmp_path):
    # 依存の辺も今回の入力を優先する: #1 の依存（#2）を外して採点し直すと、#1 が先に並ぶ
    fake.issue(1, milestone="01")
    fake.issue(2, milestone="01")
    rows = [{"number": 1, "ubv": 5, "tc": 1, "rr_oe": 1, "size": 1, "digest": "d1", "depends_on": [2]},
            {"number": 2, "ubv": 1, "tc": 1, "rr_oe": 1, "size": 1, "digest": "d2"}]  # fmt: skip
    first = run_issues("rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", rows))
    assert [r["number"] for r in first["items"][0]["rows"]] == [2, 1]
    rows[0] = {k: v for k, v in rows[0].items() if k != "depends_on"}
    again = run_issues("rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", rows))
    assert [r["number"] for r in again["items"][0]["rows"]] == [1, 2]


def test_rank_reads_capacity_and_label_floor_from_the_declaration(repo_dir, fake, run_issues, tmp_path):
    (repo_dir / ".ndf").mkdir(exist_ok=True)
    (repo_dir / ".ndf" / "backlog.json").write_text(json.dumps({"capacity": 1, "label_floor": {"bug": 13}}))
    fake.issue(1, milestone="01")
    fake.issue(2, milestone="01", labels=("bug",))
    rows = [{"number": n, "ubv": 5 if n == 1 else 1, "tc": 1, "rr_oe": 1, "size": 1, "digest": f"d{n}"} for n in (1, 2)]
    got = run_issues("rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", rows))
    table = got["items"][0]
    assert [r["number"] for r in table["rows"]] == [2, 1] and table["boundary"]["capacity"] == 1
