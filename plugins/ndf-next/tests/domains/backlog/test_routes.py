"""候補の経路 diff-identifier・sub-issue・closed-milestone と、親 → 子の辺を順位の依存に入れる処理。"""

from __future__ import annotations

import json
from pathlib import Path

from ndf.domains.backlog import cli, collect

AFTER, BEFORE = "2999-01-01T00:00:00Z", "2000-01-01T00:00:00Z"


def _run(capsys, last_result, *argv: str) -> dict:
    cli.main(list(argv))
    return last_result(capsys.readouterr().out)


def test_removed_identifiers_keep_only_tokens_gone_from_the_diff():
    diff = "--- a/x.py\n+++ b/x.py\n-def old_helper(a): return keep_this_one\n+def renamed(a): return keep_this_one\n-plain word\n"
    assert collect.removed_identifiers(diff) == {"old_helper"}


def _since(repo_dir: Path, git_in) -> str:
    (repo_dir / "lib.py").write_text("def old_helper():\n    return still_here_name\n")
    (repo_dir / "other.py").write_text("still_here_name = 1\n")
    git_in(repo_dir, "add", ".")
    git_in(repo_dir, "commit", "-q", "-m", "add")
    base = git_in(repo_dir, "rev-parse", "HEAD").strip()
    (repo_dir / "lib.py").write_text("def new_helper():\n    return 1\n")
    git_in(repo_dir, "commit", "-q", "-am", "rename")
    return base


def test_candidates_pick_identifier_sub_issue_and_closed_milestone(repo_dir, git_in, fake, capsys, last_result):
    base = _since(repo_dir, git_in)
    fake.issue(1, body="old_helper を消す", milestone="M")
    fake.issue(2, body="still_here_name を直す", milestone="M")
    fake.issue(3, milestone="N", parent=5)
    fake.issue(4, milestone="K", body="親の課題: #6")
    fake.issue(5, milestone="K", state="closed", closed_at=AFTER)
    fake.issue(6, milestone="M", state="closed", closed_at=AFTER)
    fake.issue(7, milestone="N", state="closed", closed_at=BEFORE)
    got = _run(capsys, last_result, "candidates", "--repo", "acme/widgets", "--since-ref", base, "--depth", "review")
    routes = {i["number"]: i["routes"] for i in got["items"]}
    assert "diff-identifier" in routes[1] and "diff-identifier" not in routes[2]
    assert "sub-issue" in routes[3] and "sub-issue" in routes[4]
    assert "closed-milestone" in routes[1] and "closed-milestone" in routes[4] and "closed-milestone" not in routes[3]


def test_diff_candidates_do_not_take_the_closed_milestone_route(repo_dir, git_in, fake, capsys, last_result):
    base = _since(repo_dir, git_in)
    fake.issue(1, milestone="M")
    fake.issue(6, milestone="M", state="closed", closed_at=AFTER)
    got = _run(capsys, last_result, "candidates", "--repo", "acme/widgets", "--since-ref", base)
    assert got["metrics"]["by_route"]["closed-milestone"] == 0


def test_rank_counts_an_open_child_as_released_by_its_open_parent(repo_dir, fake, capsys, last_result, tmp_path):
    fake.issue(10, milestone="01")
    fake.issue(11, milestone="01", parent=10)
    rows = [{"number": n, "ubv": 1, "tc": 1, "rr_oe": 1, "size": 1, "digest": f"d{n}"} for n in (10, 11)]
    scores = tmp_path / "s.json"
    scores.write_text(json.dumps({"issues": rows}))
    got = _run(capsys, last_result, "rank", "--repo", "acme/widgets", "--scores", str(scores))
    by_num = {r["number"]: r for r in got["items"][0]["rows"]}
    assert by_num[11]["sources"]["rr_oe"] == "dependents" and by_num[10]["sources"]["rr_oe"] == "llm"
    assert [r["number"] for r in got["items"][0]["rows"]] == [11, 10]


def test_child_depending_on_its_parent_does_not_make_a_cycle(repo_dir, fake, capsys, last_result, tmp_path):
    fake.issue(10, milestone="01")
    fake.issue(11, milestone="01", parent=10)
    rows = [{"number": 10, "ubv": 1, "tc": 1, "rr_oe": 1, "size": 1, "digest": "d10"},
            {"number": 11, "ubv": 1, "tc": 1, "rr_oe": 1, "size": 1, "digest": "d11", "depends_on": [10]}]  # fmt: skip
    scores = tmp_path / "s.json"
    scores.write_text(json.dumps({"issues": rows}))
    assert _run(capsys, last_result, "rank", "--repo", "acme/widgets", "--scores", str(scores))["status"] == "ok"
