"""マージの承認の証跡（`trail.MergeRow` の読み書きと判定）を確かめる（#4409 の決定 7）。"""

from __future__ import annotations

import pytest

from ndf.adapters.github import Authored
from ndf.domains.release import approval_decide, trail

from ._fakes import HEAD, OTHER, FakeHub, pull

TARGET = trail.MergeTarget("o/r", 7, "main", HEAD)


def _row(answer: str = "approved", by: str = "user", target: trail.MergeTarget = TARGET, production: bool = True, record: str = "q1"):
    return trail.MergeRow(answer, by, target, production, record)


def _hub(*bodies: str, author: str = "octocat", edited: bool = False) -> FakeHub:
    hub = FakeHub(pulls={7: pull()})
    hub.comments_of[7] = [Authored(author, "OWNER", b, "", edited) for b in bodies]
    return hub


def test_rendered_row_reads_back():
    row = _row()
    hub = _hub(trail.render_merge_row(row))
    assert trail.merge_rows(hub, 7, "octocat") == [row]


def test_rendered_row_does_not_carry_the_release_approval_mark():
    body = trail.render_merge_row(_row())
    assert trail.MERGE_MARK in body
    assert "ndf:release-approval" not in body


def test_merge_rows_skip_edited_and_other_writers():
    body = trail.render_merge_row(_row())
    assert trail.merge_rows(_hub(body, edited=True), 7, "octocat") == []
    # 書き込み権限を持つ別の login の証跡も数えない（同じ login だけ）
    hub = _hub(body, author="someone")
    hub.trusted.add("someone")
    assert trail.merge_rows(hub, 7, "octocat") == []


def test_merge_rows_skip_release_approval_trails_and_broken_rows():
    release = approval_decide.render_trail("approved", HEAD, "user")
    broken = f"{trail.MERGE_MARK}\n回答: approved\n"
    assert trail.merge_rows(_hub(release, broken), 7, "octocat") == []


def test_mvv_rows_count_only_inside_the_mvv_note():
    alone = trail.render_merge_row(_row(by="mvv"))
    with_note = "## MVV 判定（リリース承認ゲート）\n\n- 判定: 従う\n\n" + alone
    assert trail.merge_rows(_hub(alone), 7, "octocat") == []
    assert [r.by for r in trail.merge_rows(_hub(with_note), 7, "octocat")] == ["mvv"]


def test_judgment_takes_the_latest_answer_for_the_same_target():
    approve, reject = _row(), _row("rejected", record="q2")
    assert trail.merge_judgment([approve], TARGET) == approve
    assert trail.merge_judgment([approve, reject], TARGET) is None
    assert trail.merge_judgment([reject, _row(record="q3")], TARGET) == _row(record="q3")


@pytest.mark.parametrize(
    "now",
    [
        TARGET._replace(head=OTHER),
        TARGET._replace(base="develop"),
        TARGET._replace(repo="o/other"),
        TARGET._replace(number=8),
    ],
)
def test_judgment_needs_the_same_target(now):
    assert trail.merge_judgment([_row()], now) is None


def test_judgment_ignores_rows_without_production_and_compares_repo_case_insensitively():
    assert trail.merge_judgment([_row(production=False)], TARGET) is None
    assert trail.merge_judgment([_row()], TARGET._replace(repo="O/R")) == _row()


def test_write_merge_row_posts_once_per_record():
    hub = FakeHub(pulls={7: pull()})
    assert trail.write_merge_row(hub, _row(), "octocat") is True
    assert trail.write_merge_row(hub, _row(), "octocat") is False
    posts = [r for r in hub.requests if r[0] == "POST"]
    assert len(posts) == 1


@pytest.mark.parametrize(("answer", "by"), [("approved", "conductor"), ("maybe", "user")])
def test_write_merge_row_refuses_bad_values(answer, by):
    hub = FakeHub(pulls={7: pull()})
    with pytest.raises(trail.TrailError):
        trail.write_merge_row(hub, _row(answer, by), "octocat")
    assert not [r for r in hub.requests if r[0] == "POST"]


def test_release_check_does_not_count_merge_rows():
    """CD の workflow が読む既存の判定（`trail.read`）は、マージの承認の証跡を数えない。"""
    hub = _hub(trail.render_merge_row(_row()))
    assert trail.read(hub, 7).judgment.state not in approval_decide.ANSWERS
