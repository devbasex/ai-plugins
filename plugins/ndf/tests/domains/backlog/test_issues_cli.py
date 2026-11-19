"""`ndf issues` のサブコマンド（候補・順位・反映・戻し・深さ）と状態の保存先（#1640・#2064・#2423・#2540・#2701・#1595・#3854）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ndf import contracts

from ndf import cli as top
from ndf.domains.backlog import cli, depth, model, state
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


def test_depth_limits_read_the_ndf_settings(repo_dir, run_issues):
    # #2423: 設定の depths.review.seconds 900 で 900、無ければ既定、形が違えば止まる
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
    path.write_text(json.dumps({"issues": rows}, ensure_ascii=False))
    return str(path)


def _est(n: int, ubv: int, tc: int = 1, rr: int = 1, size: int = 1, digest: str | None = None, **extra) -> dict:
    """手順書（ranking.md）の形の見積の入力の 1 件。"""
    col = lambda step: {"step": step, "why": f"#{n} の根拠"}  # noqa: E731
    return {"number": n, "digest": digest or f"d{n}", "ubv": col(ubv), "tc": col(tc), "rr_oe": col(rr), "size": col(size), **extra}


def _candidates(run_issues, *extra: str) -> dict:
    """apply の前に記録のリポジトリ（candidates.json の metrics.repo）を残す。"""
    return run_issues("candidates", "--repo", "acme/widgets", *extra)


def test_review_rank_asks_to_rescore_stale_estimates(repo_dir, fake, run_issues, tmp_path):
    # #2540: 見積の欠けた課題を残したまま見直しの順位を打つと、採点を求める形で返る
    fake.issue(1, milestone="01")
    fake.issue(2, milestone="01")
    sd = _sd(repo_dir)
    sd.mkdir(parents=True)
    (sd / "estimates.json").write_text(json.dumps({"2": {"number": 2, "ubv": 3, "tc": 1, "rr_oe": 1, "size": 1, "digest": "old"}}))
    cand = run_issues("candidates", "--repo", "acme/widgets")
    digest = {i["number"]: i["digest"] for i in cand["items"]}
    scores = _scores(tmp_path / "s.json", [_est(1, 3, digest=digest[1])])
    got = run_issues("rank", "--repo", "acme/widgets", "--scores", scores, "--depth", "review")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "incomplete"
    assert [g["name"] for g in got["gaps"]] == ["#2"]
    assert run_issues("rank", "--repo", "acme/widgets", "--scores", scores)["status"] == "ok"


def _ranked(repo_dir, fake, run_issues, tmp_path) -> dict:
    fake.issue(1, milestone="01 直近")
    fake.issue(2, milestone="01 直近")
    fake.issue(9, milestone="02 次")
    fake.milestones = {"01 直近": 1, "02 次": 2}
    rows = [_est(n, u) for n, u in ((1, 2), (2, 1), (9, 20))]
    return run_issues("rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", rows), "--capacity", "2")


def test_apply_records_rejections_and_moves_approved(repo_dir, fake, run_issues, tmp_path):
    ranked = _ranked(repo_dir, fake, run_issues, tmp_path)
    assert [f["number"] for f in ranked["metrics"]["forward"]] == [9]
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"rank": ranked["metrics"]["digest"], "rejected": [{"number": 9, "direction": FORWARD}]}))
    _candidates(run_issues)
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
    _candidates(run_issues)
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
    _candidates(run_issues)
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
    _candidates(run_issues)
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
    rows = [_est(n, u) for n, u in ((1, 2), (2, 1), (9, 20))]
    ranked = run_issues("rank", "--repo", "acme/widgets", "--state-dir", str(sd), "--scores", _scores(tmp_path / "s.json", rows),
                        "--capacity", "2")  # fmt: skip
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"rank": ranked["metrics"]["digest"], "reschedule": [{"number": 9, "direction": FORWARD, "milestone": "01 直近"}]})
    )
    _candidates(run_issues, "--state-dir", str(sd))
    got = run_issues("apply", "--repo", "acme/widgets", "--state-dir", str(sd), "--plan", str(plan))
    assert got["status"] == "gate" and got["metrics"]["held"] == [9] and fake.issues[9]["milestone"] == "02 次"
    argv = got["routes"]["approved"]["argv"]
    assert argv[argv.index("--state-dir") + 1] == str(sd)


def test_rescoring_without_a_dependency_drops_the_stored_edge(repo_dir, fake, run_issues, tmp_path):
    # 依存の辺も今回の入力を優先する: #1 の依存（#2）を外して採点し直すと、#1 が先に並ぶ
    fake.issue(1, milestone="01")
    fake.issue(2, milestone="01")
    rows = [_est(1, 5, depends_on=[2]), _est(2, 1)]
    first = run_issues("rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", rows))
    assert [r["number"] for r in first["items"][0]["rows"]] == [2, 1]
    rows[0] = {k: v for k, v in rows[0].items() if k != "depends_on"}
    again = run_issues("rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", rows))
    assert [r["number"] for r in again["items"][0]["rows"]] == [1, 2]


def test_rank_reads_capacity_and_label_floor_from_the_ndf_settings(repo_dir, fake, run_issues, tmp_path):
    (repo_dir / ".ndf").mkdir(exist_ok=True)
    (repo_dir / ".ndf" / "backlog.json").write_text(json.dumps({"capacity": 1, "label_floor": {"bug": 13}}))
    fake.issue(1, milestone="01")
    fake.issue(2, milestone="01", labels=("bug",))
    rows = [_est(n, 5 if n == 1 else 1) for n in (1, 2)]
    got = run_issues("rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", rows))
    table = got["items"][0]
    assert [r["number"] for r in table["rows"]] == [2, 1] and table["boundary"]["capacity"] == 1


def _rank_auto(fake, run_issues, tmp_path, approved: str | None = None) -> dict:
    # 直近に #1・#2、次に #9（利用者へ届いた不具合で UBV 20。前倒しは自動）
    fake.issue(1, milestone="01 直近")
    fake.issue(2, milestone="01 直近")
    fake.issue(9, milestone="02 次")
    fake.milestones = {"01 直近": 1, "02 次": 2}
    rows = [_est(n, u, observed_harm=n == 9) for n, u in ((1, 2), (2, 1), (9, 20))]
    extra = ["--approved", approved] if approved else []
    return run_issues("rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", rows), "--capacity", "2", *extra)


def _apply_plan(run_issues, tmp_path, plan: dict) -> dict:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False))
    _candidates(run_issues)
    return run_issues("apply", "--repo", "acme/widgets", "--plan", str(path))


def test_rejecting_an_auto_move_writes_the_tables_without_a_gate(repo_dir, fake, run_issues, tmp_path):
    # 自動の前倒しを退けただけの apply は承認待ちで止まらず、#9 を移す前の所属で表を書く
    ranked = _rank_auto(fake, run_issues, tmp_path)
    assert ranked["metrics"]["forward"][0]["decision"] == model.AUTO
    got = _apply_plan(run_issues, tmp_path, {"rank": ranked["metrics"]["digest"], "rejected": [{"number": 9, "direction": FORWARD}]})
    assert got["status"] == "ok" and sorted(got["metrics"]["tables"]["written"]) == ["01 直近", "02 次"]
    assert "#9" not in fake.descriptions[1] and "#9" in fake.descriptions[2] and fake.issues[9]["milestone"] == "02 次"


def test_tables_drop_issues_closed_by_the_same_apply(repo_dir, fake, run_issues, tmp_path):
    ranked = _rank_auto(fake, run_issues, tmp_path)
    i = fake.issues[2]
    close = {"number": 2, "verdict": "閉じてよい", "updated_at": i["updated"], "digest": model.snapshot_digest(i),
             "changes": {"state": "closed", "state_reason": "completed"}}  # fmt: skip
    plan = {"rank": ranked["metrics"]["digest"], "rejected": [{"number": 9, "direction": FORWARD}], "actions": [close]}
    got = _apply_plan(run_issues, tmp_path, plan)
    assert got["status"] == "ok" and got["metrics"]["closed"] == [2]
    assert "#1" in fake.descriptions[1] and "#2" not in fake.descriptions[1]


def test_a_move_does_not_overwrite_a_milestone_changed_after_rank(repo_dir, fake, run_issues, tmp_path):
    ranked = _rank_auto(fake, run_issues, tmp_path)
    fake.milestones["03 他"] = 3
    fake.issues[9]["milestone"] = "03 他"
    plan = {"rank": ranked["metrics"]["digest"], "reschedule": [{"number": 9, "direction": FORWARD, "milestone": "01 直近"}]}
    got = _apply_plan(run_issues, tmp_path, plan)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "incomplete" and [g["name"] for g in got["gaps"]] == ["#9"]
    assert fake.issues[9]["milestone"] == "03 他" and "#9" not in fake.descriptions[1]


def test_a_label_only_change_after_the_plan_is_skipped(repo_dir, fake, run_issues, tmp_path):
    # 題・本文が同じでも、plan の後にラベルだけ変わった課題は書かずに飛ばす
    fake.issue(5, labels=("keep",))
    i = fake.issues[5]
    act = {"number": 5, "verdict": "追記が要る", "updated_at": i["updated"], "digest": model.snapshot_digest(i),
           "changes": {"remove_labels": ["keep"]}}  # fmt: skip
    fake.issue(5, labels=("keep", "human"), updated="2026-11-02T00:00:00Z")
    got = _apply_plan(run_issues, tmp_path, {"actions": [act]})
    assert got["status"] == "gate" and got["metrics"]["skipped_changed"] == [5] and fake.issues[5]["labels"] == ["keep", "human"]


# --- #3854: 手順書の形の見積の入力・差分の棚卸しの起点・説明の表からの読み戻し・書き込み先の突き合わせ ---

RANKING_MD_SCORES = """{"version": 1,
 "issues": [{"number": 1399, "digest": "<candidates の値>",
             "ubv": {"step": 13, "why": "取り消した変更が origin へ出た（安全機構の欠落）", "impact": "全体"},
             "tc": {"step": 8, "why": "..."}, "rr_oe": {"step": 8, "why": "..."},
             "size": {"step": 5, "why": "<閉じた課題の番号>（差分 <行数>・<所要>・<費用>）と同じ程度"},
             "mitigation": "無い", "observed_harm": true, "depends_on": [1237],
             "waiting_decision": false}]}"""


def _created(fake, created: dict[int, str]) -> None:
    """偽の GitHub が返す課題の作成の時刻を課題ごとに変える。"""
    node = fake._node
    fake._node = lambda n: {**node(n), "createdAt": created[n]} if n in created and node(n) else node(n)


def test_rank_reads_the_ranking_md_example_as_written(repo_dir, fake, run_issues, tmp_path):
    # #3854 の条件 1: ranking.md の「見積の入力」の例をそのまま渡すと、その課題が順位に入る
    fake.issue(1399, milestone="01")
    path = tmp_path / "s.json"
    path.write_text(RANKING_MD_SCORES)
    got = run_issues("rank", "--repo", "acme/widgets", "--scores", str(path))
    assert got["status"] == "ok" and got["metrics"]["excluded"] == []
    assert [r["number"] for r in got["items"][0]["rows"]] == [1399]


def test_rank_excludes_empty_reasons_integer_steps_and_out_of_band_ubv(repo_dir, fake, run_issues, tmp_path):
    # #3854 の条件 2・3・8: 根拠の欠け・整数の形・帯の外は excluded に理由つきで入り、差分の棚卸しでも incomplete（終了コード 6）
    for n in (1, 2, 3, 4, 5):
        fake.issue(n, milestone="01")
    rows = [{**_est(1, 3), "tc": {"step": 1}}, {**_est(2, 3), "ubv": 5}, _est(3, 3), _est(4, 3, mitigation="防いでいる"), _est(5, 3)]
    for row, impact in zip(rows[2:], ("機能", "機能", "表示")):
        row["ubv"]["impact"] = impact
    got = run_issues("rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", rows), "--depth", "diff")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "incomplete"
    reasons = {e["number"]: e["reason"] for e in got["metrics"]["excluded"]}
    assert sorted(reasons) == [1, 2, 3]
    assert "根拠（why）が空" in reasons[1] and '{"step", "why"}' in reasons[2] and "帯 5〜8 の外" in reasons[3]
    assert [g["name"] for g in got["gaps"]] == ["#1", "#2", "#3"]
    assert cli.main(["rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", rows)]) == 6


def test_harm_guide_in_the_settings_is_accepted_by_the_schema_and_changes_the_band(repo_dir, fake, run_issues, tmp_path):
    # #3854 の条件 4: harm_guide を書いた設定をスキーマと rank が受け、帯が設定の値に変わる
    decl = {"harm_guide": {"全体": [13, 20], "機能": [2, 8], "表示": [1, 1], "なし": [1, 1]}}
    Draft202012Validator(contracts.load_json("schemas/ndf-settings/backlog.schema.json")).validate(decl)
    (repo_dir / ".ndf").mkdir(exist_ok=True)
    (repo_dir / ".ndf" / "backlog.json").write_text(json.dumps(decl, ensure_ascii=False))
    fake.issue(1, milestone="01")
    row = _est(1, 2)
    row["ubv"]["impact"] = "機能"
    got = run_issues("rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", [row]))
    assert got["status"] == "ok" and got["metrics"]["excluded"] == []


def test_custom_scale_without_harm_guide_ranks_with_a_warning_and_a_written_guide_off_scale_is_invalid(
    repo_dir, fake, run_issues, tmp_path
):
    custom = {"scale": [1, 2, 4, 8, 16], "dependents_steps": [[0, 1]], "auto_threshold": 8, "large_min_size": 8}
    (repo_dir / ".ndf").mkdir(exist_ok=True)
    (repo_dir / ".ndf" / "backlog.json").write_text(json.dumps(custom))
    fake.issue(1, milestone="01")
    row = _est(1, 16)
    row["ubv"]["impact"] = "表示"
    scores = _scores(tmp_path / "s.json", [row])
    got = run_issues("rank", "--repo", "acme/widgets", "--scores", scores)
    assert got["status"] == "ok" and len(got["warnings"]) == 1 and "harm_guide" in got["warnings"][0]
    (repo_dir / ".ndf" / "backlog.json").write_text(json.dumps({**custom, "harm_guide": {"全体": [13, 20]}}, ensure_ascii=False))
    bad = run_issues("rank", "--repo", "acme/widgets", "--scores", scores)
    assert bad["status"] == "stopped" and bad["cause"]["kind"] == "invalid"


def test_reasons_and_impact_stay_in_the_record_and_the_next_run_checks_them(repo_dir, fake, run_issues, tmp_path):
    # #3854 の条件 5: 記録（estimates.json）が根拠と区分を持ち、次の回に記録から採った見積も帯で確かめる
    fake.issue(1, milestone="01")
    row = _est(1, 5)
    row["ubv"]["impact"] = "機能"
    assert run_issues("rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", [row]))["status"] == "ok"
    kept = json.loads((_sd(repo_dir) / "estimates.json").read_text())["1"]
    assert kept["ubv"] == {"step": 5, "why": "#1 の根拠", "impact": "機能"} and kept["tc"]["why"] == "#1 の根拠"
    again = run_issues("rank", "--repo", "acme/widgets")
    assert again["status"] == "ok" and again["metrics"]["restored"] == []
    (repo_dir / ".ndf").mkdir(exist_ok=True)
    (repo_dir / ".ndf" / "backlog.json").write_text(json.dumps({"harm_guide": {"機能": [8, 13]}}, ensure_ascii=False))
    out = run_issues("rank", "--repo", "acme/widgets")
    assert out["status"] == "stopped" and "帯 8〜13 の外" in out["metrics"]["excluded"][0]["reason"]


def test_diff_rank_leaves_issues_before_the_since_ref_for_the_review(repo_dir, fake, git_in, run_issues, tmp_path):
    # #3854 の条件 6・8・I9: 見積の無い起点以前の課題は left_for_review へ。excluded にも順位にも入らない
    since = git_in(repo_dir, "rev-parse", "HEAD").strip()
    for n in (1, 2, 3):
        fake.issue(n, milestone="01")
    _created(fake, {1: "2000-01-01T00:00:00Z", 2: "2000-01-01T00:00:00Z", 3: "2999-01-01T00:00:00Z"})
    scores = _scores(tmp_path / "s.json", [_est(2, 3)])
    got = run_issues("rank", "--repo", "acme/widgets", "--scores", scores, "--depth", "diff", "--since-ref", since)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "incomplete"
    assert got["metrics"]["left_for_review"] == [1] and [e["number"] for e in got["metrics"]["excluded"]] == [3]
    fake.issues.pop(3)
    ok = run_issues("rank", "--repo", "acme/widgets", "--scores", scores, "--depth", "diff", "--since-ref", since)
    assert ok["status"] == "ok" and ok["metrics"]["left_for_review"] == [1] and ok["metrics"]["excluded"] == []
    assert [r["number"] for r in ok["items"][0]["rows"]] == [2]
    bad = run_issues("rank", "--repo", "acme/widgets", "--scores", scores, "--since-ref", "no-such-ref")
    assert bad["status"] == "stopped" and bad["cause"]["kind"] == "invalid"


def test_diff_candidates_keep_unscored_issues_after_the_since_ref(repo_dir, fake, git_in, run_issues, tmp_path):
    # #3854 の条件 7: 差分の棚卸しの unscored は起点の時刻より後に作られた課題だけで、metrics.since を返す
    since = git_in(repo_dir, "rev-parse", "HEAD").strip()
    for n in (1, 2, 3):
        fake.issue(n, milestone="01")
    _created(fake, {1: "2000-01-01T00:00:00Z", 2: "2999-01-01T00:00:00Z", 3: "2000-01-01T00:00:00Z"})
    run_issues("rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", [_est(3, 3)]))
    got = run_issues("candidates", "--repo", "acme/widgets", "--since-ref", since)
    assert {i["number"] for i in got["items"] if "unscored" in i["routes"]} == {2}
    stamp = git_in(repo_dir, "log", "-1", "--format=%ct").strip()
    assert got["metrics"]["since"] == datetime.fromtimestamp(int(stamp), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    review = run_issues("candidates", "--repo", "acme/widgets", "--since-ref", since, "--depth", "review")
    assert {i["number"] for i in review["items"] if "unscored" in i["routes"]} == {1, 2, 3}
    assert run_issues("candidates", "--repo", "acme/widgets")["metrics"]["since"] is None


def _table_desc(rows: list[tuple[int, str, str, str, str]]) -> str:
    """11 系の apply が書く形の `### 順位` の表（課題・UBV・TC・RR/OE・Size）。"""
    lines = ["| 順位 | 課題 | UBV | TC | RR/OE | Size | CoD | 順位の値 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {k} | #{n} | {u} | {t} | {r} | {s} | 9 | 3.0 |" for k, (n, u, t, r, s) in enumerate(rows, 1)]
    return "目的の文\n\n### 順位\n\n" + "\n".join(lines) + "\n"


@pytest.mark.parametrize("depth", ["diff", "review"])
def test_rank_restores_estimates_from_the_milestone_table_without_records(repo_dir, fake, run_issues, tmp_path, depth):
    # #3854 の条件 9: 記録の無い手元で、説明の表に行を持つ課題は excluded に入らず restored に載る。マーカーの付いた段階は尺度の最小へ戻る
    fake.issue(1, milestone="01")
    fake.issue(2, milestone="01")
    fake.issue(3, milestone="01")
    fake.milestones = {"01": 1}
    fake.descriptions = {1: _table_desc([(1, "8", "3", "1", "2"), (2, "13（ラベル）", "1", "5（被依存）", "3（見積もり）")])}
    cand = _candidates(run_issues, "--add", "3", "--depth", depth)
    digest = {i["number"]: i["digest"] for i in cand["items"]}
    got = run_issues(
        "rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", [_est(3, 2, digest=digest[3])]), "--depth", depth
    )
    assert got["status"] == "ok" and got["metrics"]["restored"] == [1, 2] and got["metrics"]["excluded"] == []
    rows = {r["number"]: r for r in got["items"][0]["rows"]}
    assert (rows[1]["ubv"], rows[1]["tc"], rows[1]["size"]) == (8, 3, 2)
    assert (rows[2]["ubv"], rows[2]["rr_oe"], rows[2]["size"]) == (1, 1, 3)


def test_apply_with_a_partial_rank_keeps_the_rows_already_in_the_table(repo_dir, fake, run_issues, tmp_path):
    # #3854 の条件 10: 一部の課題だけに見積を付けた rank の digest で apply しても、表に行を持っていた課題の行が消えない
    for n in (1, 2, 3):
        fake.issue(n, milestone="01")
    fake.milestones = {"01": 1}
    fake.descriptions = {1: _table_desc([(1, "8", "3", "1", "2"), (2, "5", "1", "1", "3")])}
    ranked = run_issues("rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", [_est(3, 2)]))
    assert ranked["status"] == "ok"
    got = _apply_plan(run_issues, tmp_path, {"rank": ranked["metrics"]["digest"]})
    assert got["status"] == "ok" and got["metrics"]["tables"]["written"] == ["01"]
    assert all(f"| #{n} |" in fake.descriptions[1] for n in (1, 2, 3))


def test_apply_refuses_a_rank_that_excluded_issues(repo_dir, fake, run_issues, tmp_path):
    # #3854 の決定 10: excluded を持つ rank の digest を入れた plan は書き込み 0 件で precondition
    fake.issue(1, milestone="01")
    fake.issue(2, milestone="01")
    fake.milestones = {"01": 1}
    ranked = run_issues("rank", "--repo", "acme/widgets", "--scores", _scores(tmp_path / "s.json", [_est(1, 2)]))
    assert ranked["status"] == "stopped" and ranked["metrics"]["digest"]
    got = _apply_plan(run_issues, tmp_path, {"rank": ranked["metrics"]["digest"]})
    assert got["status"] == "stopped" and got["cause"]["kind"] == "precondition" and fake.writes == []


def test_apply_checks_the_target_against_the_recorded_repository(repo_dir, fake, run_issues, tmp_path):
    # #3854 の条件 11・I10: plan の repo を受け、記録が無い・plan の repo が違うと 1 件も書かずに precondition（終了コード 3）
    fake.issue(10)
    fake.issue(20)
    path = tmp_path / "plan.json"
    plan = {"repo": "acme/widgets", "reparent": [{"number": 20, "parent": 10}]}
    path.write_text(json.dumps(plan))
    none = run_issues("apply", "--repo", "acme/widgets", "--plan", str(path))
    assert none["status"] == "stopped" and none["cause"]["kind"] == "precondition" and fake.writes == []
    _candidates(run_issues)
    path.write_text(json.dumps({**plan, "repo": "acme/other"}))
    assert cli.main(["apply", "--repo", "acme/widgets", "--plan", str(path)]) == 3
    assert fake.writes == []
    sd = _sd(repo_dir)
    rec = json.loads((sd / "candidates.json").read_text())
    (sd / "candidates.json").write_text(json.dumps({**rec, "metrics": {**rec["metrics"], "repo": "acme/other"}}))
    path.write_text(json.dumps(plan))
    other = run_issues("apply", "--repo", "acme/widgets", "--plan", str(path))
    assert other["status"] == "stopped" and other["cause"]["kind"] == "precondition" and fake.writes == []
    (sd / "candidates.json").write_text(json.dumps(rec))
    assert run_issues("apply", "--repo", "acme/widgets", "--plan", str(path))["status"] == "ok"
    assert fake.issues[20]["parent"] == 10
