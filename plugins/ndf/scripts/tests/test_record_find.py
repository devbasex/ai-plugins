"""まとめの差し込み `{queue_record}` と、リリース記録を置いた PR の探し方（#2230 の I3〜I5・決定 2・3）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import gh_call  # noqa: E402
from sprint_close_lib import record_find  # noqa: E402
from sprint_close_lib.record_find import RecordHit, find_record  # noqa: E402
from supervise_lib import queue  # noqa: E402
from supervise_lib.plan import leftover_placeholders  # noqa: E402
from supervise_lib.queue_fill import fill_queue_pr  # noqa: E402

REPO = "o/r"


def record(stage="本番（npm）", sprint=(10,)):
    return f"## 配布の記録\n\n段階: {stage}\n版: 1.0.0 → 1.1.0（minor）\nスプリント: PR " + " / ".join(f"#{n}" for n in sprint) + "\n"


class FakeGh:
    """課題と PR の履歴の参照（refs: 番号 → [(PR, 本文)]）と、PR の本文とコメント（prs: 番号 → (本文, [(識別子, 本文)])）。"""

    def __init__(self, refs, prs=None, fail=()):
        self.refs, self.prs, self.fail, self.calls = refs, prs or {}, set(fail), []

    def __call__(self, args, stdin=None, cwd=None):
        self.calls.append(args)
        if args[:2] == ["repo", "view"]:
            return gh_call.GhResult(0, REPO + "\n", "")
        if args[0] == "api":
            n = int(args[-1].split("/")[-2])
            if n in self.fail:
                return gh_call.GhResult(1, "", "HTTP 502")
            events = [
                {
                    "event": "cross-referenced",
                    "source": {"issue": {"number": pr, "body": body, "pull_request": {}, "repository": {"full_name": REPO}}},
                }
                for pr, body in self.refs.get(n, [])
            ]
            return gh_call.GhResult(0, json.dumps([events + [{"event": "labeled"}]]), "")
        if args[:2] == ["pr", "view"]:
            body, comments = self.prs.get(int(args[2]), ("", []))
            cs = [{"body": b, "url": f"https://github.com/{REPO}/pull/{args[2]}#issuecomment-{cid}"} for cid, b in comments]
            return gh_call.GhResult(0, json.dumps({"body": body, "comments": cs}), "")
        raise AssertionError(args)


@pytest.fixture
def fake(monkeypatch):
    def install(refs, prs=None, fail=()):
        g = FakeGh(refs, prs, fail)
        monkeypatch.setattr(gh_call, "gh", g)
        return g

    return install


def pr_views(g):
    return [int(a[2]) for a in g.calls if a[:2] == ["pr", "view"]]


def test_skips_the_later_sprint_reached_through_a_pr_that_only_refers_to_the_issue(fake):
    """#2089 の形: 閉じる PR #2103 の記録 #2126 を採り、参照だけの #2128 を通じた後のスプリントの記録 #2138 を採らない。"""
    g = fake(
        {
            2089: [(2103, "Closes #2089"), (2128, "#2089 の確定仕様化"), (2091, "refs #2089")],
            2103: [(2120, "#2103"), (2126, "#2103")],
            2128: [(2138, "#2128")],
        },
        {2126: ("", [(500, record(sprint=(2098, 2103)))]), 2138: ("", [(900, record(sprint=(2128,)))])},
    )
    hit = find_record(".", None, [2089])
    assert hit.pr == 2126 and hit.closers == (2103,)
    assert 2138 not in pr_views(g)


def test_two_sprints_closing_the_same_issue_are_not_guessed(fake):
    """課題を持ち越して 2 つのスプリントで閉じる PR を出した形: どちらも採らず、理由に両方の記録の PR が載る。"""
    fake(
        {5: [(10, "Closes #5"), (20, "最終工程の後に閉じる: #5")], 10: [(30, "#10")], 20: [(40, "#20")]},
        {30: ("", [(100, record(sprint=(10,)))]), 40: ("", [(200, record(sprint=(20, 21)))])},
    )
    hit = find_record(".", None, [5])
    assert hit.pr is None and [pr for pr, _ in hit.ambiguous] == [30, 40]
    assert "PR #30（スプリント: #10）" in hit.reason() and "PR #40（スプリント: #20 #21）" in hit.reason()


def test_a_record_covering_only_some_of_the_issues_is_not_taken(fake):
    fake(
        {5: [(10, "Closes #5")], 6: [(11, "Fixes #6")], 10: [(30, "#10")]},
        {30: ("", [(100, record(sprint=(10,)))])},
    )
    assert find_record(".", None, [5, 6]).pr is None


def test_an_issue_without_a_closing_pr_does_not_block_the_record(fake):
    fake(
        {5: [(10, "Closes #5")], 7: [(12, "#7 に触れた")], 10: [(30, "#10")]},
        {30: ("", [(100, record(sprint=(10,)))])},
    )
    assert find_record(".", None, [5, 7]).pr == 30


def test_no_closing_pr_at_all_reads_no_record(fake):
    g = fake({5: [(12, "#5 に触れた")]})
    hit = find_record(".", None, [5])
    assert hit.pr is None and pr_views(g) == [] and "#5 を閉じる PR が見つからない" in hit.reason()


def test_a_record_before_the_production_release_is_not_taken(fake):
    fake({5: [(10, "Closes #5")], 10: [(30, "#10")]}, {30: ("", [(100, record(stage="検証（staging）"))])})
    hit = find_record(".", None, [5])
    assert hit.pr is None and hit.pre_release == (30,)
    assert "閉じる PR #10 と、それを参照する PR #30 を読んだ" in hit.reason() and "本番の前の記録だけ: PR #30" in hit.reason()


def test_the_record_in_the_latest_comment_wins_over_a_body_record(fake):
    fake(
        {5: [(10, "Closes #5")], 10: [(30, "#10"), (40, "#10")]},
        {30: ("", [(900, record())]), 40: (record(), [])},
    )
    assert find_record(".", None, [5]).pr == 30


def test_github_errors_are_reported(fake):
    fake({}, fail=[5])
    hit = find_record(".", None, [5])
    assert hit.pr is None and hit.reason().startswith("GitHub を読めない: HTTP 502")


def close_plan(tmp_path, issues=(5,)):
    p = tmp_path / "close.json"
    steps = [
        {"id": "release-verify", "type": "work", "prompt": "配布の記録は Pull Request #{queue_record} にある。"},
        {"id": "close", "type": "run", "cmd": "sprint-close.py --record-pr {queue_record} --issues 5"},
    ]
    p.write_text(json.dumps({"課題": list(issues), "リポジトリ": str(tmp_path), "steps": steps}, ensure_ascii=False))
    return p


def test_fill_writes_the_found_record_into_every_step(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(record_find, "find_record", lambda root, repo, issues: (seen.append((root, issues)), RecordHit(pr=30))[1])
    p = close_plan(tmp_path)
    assert fill_queue_pr(str(p), []) is None
    steps = json.loads(p.read_text())["steps"]
    assert "#30 にある" in steps[0]["prompt"] and "--record-pr 30 " in steps[1]["cmd"]
    assert seen == [(str(tmp_path), [5])]


def test_fill_leaves_the_plan_and_the_stage_is_unfilled_when_no_record_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr(record_find, "find_record", lambda root, repo, issues: RecordHit(issues=(5,)))
    p = close_plan(tmp_path)
    before = p.read_text()
    skipped, runnable = queue.fill_stage(True, [str(p)], [])
    assert runnable == [] and skipped[0]["unfilled"] is True
    assert "{queue_record}（課題 #5 を閉じる PR が見つからない" in skipped[0]["reason"]
    assert p.read_text() == before and leftover_placeholders(before) == ["{queue_record}"]


def test_plans_without_the_marker_do_not_read_github(tmp_path, monkeypatch):
    monkeypatch.setattr(record_find, "find_record", lambda *a: pytest.fail("GitHub を読んだ"))
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"課題": [5], "steps": [{"id": "a", "cmd": "sprint-close.py --record-pr unknown"}]}))
    assert fill_queue_pr(str(p), []) is None
