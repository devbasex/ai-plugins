"""見直し・大整理を起動する判定（`ndf issues due`）。持ち越しの数が見積の欠けだけの課題を数えない（#2540）。"""

from __future__ import annotations

import json
from pathlib import Path


def _declare(root: Path, depths: dict) -> None:
    path = root / ".ndf" / "backlog.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"depths": depths}))


def _sd(root: Path) -> Path:
    return root / ".ndf" / "backlog" / "state" / "acme__widgets"


def test_carry_of_unscored_issues_does_not_start_the_next_review(repo_dir, fake, run_issues):
    # #2540 の受け入れ条件 2: 見積の欠けだけで持ち越した課題では、次の見直しがすぐに起動しない
    _declare(repo_dir, {"review": {"carry_factor": 1}})
    for n in range(1, 6):
        fake.issue(n, milestone="01")
    _sd(repo_dir).mkdir(parents=True)
    (_sd(repo_dir) / "estimates.json").write_text(json.dumps({"1": {"number": 1, "digest": "x"}}))
    cand = run_issues("candidates", "--repo", "acme/widgets", "--limit", "1")
    assert len(cand["metrics"]["deferred"]) == 4 and cand["metrics"]["carry"] == 0
    got = run_issues("due", "review", "--repo", "acme/widgets")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "not_applicable" and got["metrics"]["values"]["carry"] == 0
    for n in range(1, 6):
        fake.issue(n)
    run_issues("candidates", "--repo", "acme/widgets", "--limit", "1")
    started = run_issues("due", "review", "--repo", "acme/widgets")
    assert started["status"] == "ok" and started["metrics"]["trigger"] == ["carry"]


def test_timed_out_review_and_manual_start_the_review(repo_dir, fake, run_issues):
    assert run_issues("due", "review", "--repo", "acme/widgets")["status"] == "stopped"
    assert run_issues("due", "review", "--repo", "acme/widgets", "--manual")["metrics"]["trigger"] == ["manual"]
    run_issues("depth-record", "--repo", "acme/widgets", "--depth", "review", "--event", "timed_out", "--left", "3")
    got = run_issues("due", "review", "--repo", "acme/widgets")
    assert got["metrics"]["trigger"] == ["timed_out"] and got["metrics"]["values"]["left"] == [3]


def test_overhaul_starts_after_enough_reviews(repo_dir, fake, run_issues):
    _declare(repo_dir, {"overhaul": {"every": 2}})
    record = ("depth-record", "--repo", "acme/widgets", "--event", "ran", "--depth")
    run_issues(*record, "review")
    assert run_issues("due", "overhaul", "--repo", "acme/widgets")["status"] == "stopped"
    run_issues(*record, "review")
    assert run_issues("due", "overhaul", "--repo", "acme/widgets")["metrics"]["trigger"] == ["count"]
    run_issues(*record, "overhaul")
    assert run_issues("due", "overhaul", "--repo", "acme/widgets")["metrics"]["values"]["count"] == 0


def test_wrong_due_settings_stop_as_invalid(repo_dir, fake, run_issues):
    _declare(repo_dir, {"review": {"carry_factor": "10"}})
    got = run_issues("due", "review", "--repo", "acme/widgets")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid"
