"""マージの承認の証跡（`merge_approval.MergeRow` の読み書きと判定）を確かめる（#4409 の決定 7）。"""

from __future__ import annotations

import pytest

from ndf.adapters.github import Authored, Viewer
from ndf.core import merge_approval
from ndf.domains.release import approval_decide, trail

from ._fakes import HEAD, OTHER, FakeHub, pull

import scenes

TARGET = merge_approval.MergeTarget("o/r", 7, "main", HEAD)
# token の利用者（`FakeHub` が書いたコメントの書き手と同じアカウント ID 1）
ME = Viewer("octocat", 1)


def _row(
    answer: str = "approved", by: str = "user", target: merge_approval.MergeTarget = TARGET, production: bool = True, record: str = "q1"
):
    return merge_approval.MergeRow(answer, by, target, production, record)


def _hub(*bodies: str, author: str = "octocat", edited: bool = False, author_id: int = 1) -> FakeHub:
    hub = FakeHub(pulls={7: pull()})
    hub.comments_of[7] = [Authored(author, "OWNER", b, "", edited, author_id) for b in bodies]
    return hub


def _rendered_row_reads_back():
    row = _row()
    hub = _hub(merge_approval.render_merge_row(row))
    assert merge_approval.merge_rows(hub, 7, ME) == [row]


def _rendered_row_does_not_carry_the_release_approval_mark():
    body = merge_approval.render_merge_row(_row())
    assert merge_approval.MERGE_MARK in body
    assert "ndf:release-approval" not in body


def _merge_rows_skip_edited_and_other_writers():
    body = merge_approval.render_merge_row(_row())
    assert merge_approval.merge_rows(_hub(body, edited=True), 7, ME) == []
    # 書き込み権限を持つ別のアカウントの証跡も数えない（同じアカウントだけ）
    hub = _hub(body, author="someone", author_id=2)
    hub.trusted.add("someone")
    assert merge_approval.merge_rows(hub, 7, ME) == []


def _an_unread_viewer_neither_counts_nor_writes_and_a_same_named_account_does_not_match():
    """r1-10・r2-1: token の利用者のアカウント ID が読めなければ数えも書きもせずに TrailError。
    ボット `ndf-app[bot]` と同じ名前の個人アカウント `ndf-app` の証跡は、アカウント ID が違うので数えない。"""
    body = merge_approval.render_merge_row(_row())
    with pytest.raises(merge_approval.TrailError, match="アカウント ID"):
        merge_approval.merge_rows(_hub(body), 7, Viewer("", 0))
    hub = FakeHub(pulls={7: pull()})
    with pytest.raises(merge_approval.TrailError):
        merge_approval.write_merge_row(hub, _row(), Viewer("", 0))
    assert not [r for r in hub.requests if r[0] == "POST"]
    bot = Viewer("ndf-app[bot]", 41)
    assert merge_approval.merge_rows(_hub(body, author="ndf-app", author_id=42), 7, bot) == []
    assert merge_approval.merge_rows(_hub(body, author="ndf-app[bot]", author_id=41), 7, bot) == [_row()]


def _merge_rows_skip_release_approval_trails_and_broken_rows():
    release = approval_decide.render_trail("approved", HEAD, "user")
    broken = f"{merge_approval.MERGE_MARK}\n回答: approved\n"
    assert merge_approval.merge_rows(_hub(release, broken), 7, ME) == []


def _mvv_rows_count_only_inside_the_mvv_note():
    alone = merge_approval.render_merge_row(_row(by="mvv"))
    with_note = "## MVV 判定（リリース承認ゲート）\n\n- 判定: 従う\n\n" + alone
    assert merge_approval.merge_rows(_hub(alone), 7, ME) == []
    assert [r.by for r in merge_approval.merge_rows(_hub(with_note), 7, ME)] == ["mvv"]


def _judgment_takes_the_latest_answer_for_the_same_target():
    approve, reject = _row(), _row("rejected", record="q2")
    assert merge_approval.merge_judgment([approve], TARGET) == approve
    assert merge_approval.merge_judgment([approve, reject], TARGET) is None
    assert merge_approval.merge_judgment([reject, _row(record="q3")], TARGET) == _row(record="q3")


def _judgment_needs_the_same_target(now):
    assert merge_approval.merge_judgment([_row()], now) is None


def _judgment_ignores_rows_without_production_and_compares_repo_case_insensitively():
    assert merge_approval.merge_judgment([_row(production=False)], TARGET) is None
    assert merge_approval.merge_judgment([_row()], TARGET._replace(repo="O/R")) == _row()


def _write_merge_row_posts_once_per_record():
    hub = FakeHub(pulls={7: pull()})
    assert merge_approval.write_merge_row(hub, _row(), ME) is True
    assert merge_approval.write_merge_row(hub, _row(), ME) is False
    posts = [r for r in hub.requests if r[0] == "POST"]
    assert len(posts) == 1


def _write_merge_row_refuses_bad_values(answer, by):
    hub = FakeHub(pulls={7: pull()})
    with pytest.raises(merge_approval.TrailError):
        merge_approval.write_merge_row(hub, _row(answer, by), ME)
    assert not [r for r in hub.requests if r[0] == "POST"]


def _release_check_does_not_count_merge_rows():
    """CD の workflow が読む既存の判定（`trail.read`）は、マージの承認の証跡を数えない。"""
    hub = _hub(merge_approval.render_merge_row(_row()))
    assert trail.read(hub, 7).judgment.state not in approval_decide.ANSWERS


@pytest.mark.parametrize(
    scenes.ARGS,
    [
        scenes.scene(_rendered_row_reads_back, id="rendered_row_reads_back"),
        scenes.scene(_rendered_row_does_not_carry_the_release_approval_mark, id="rendered_row_does_not_carry_the_release_approval_mark"),
        scenes.scene(_merge_rows_skip_edited_and_other_writers, id="merge_rows_skip_edited_and_other_writers"),
        scenes.scene(
            _an_unread_viewer_neither_counts_nor_writes_and_a_same_named_account_does_not_match,
            id="an_unread_viewer_neither_counts_nor_writes_and_a_same_named_account_does_not_match",
        ),
        scenes.scene(
            _merge_rows_skip_release_approval_trails_and_broken_rows, id="merge_rows_skip_release_approval_trails_and_broken_rows"
        ),
        scenes.scene(_mvv_rows_count_only_inside_the_mvv_note, id="mvv_rows_count_only_inside_the_mvv_note"),
        scenes.scene(_judgment_takes_the_latest_answer_for_the_same_target, id="judgment_takes_the_latest_answer_for_the_same_target"),
        *[
            scenes.scene(_judgment_needs_the_same_target, id=f"judgment_needs_the_same_target-{label}", now=v)
            for label, v in zip(
                ["other-head", "other-base", "other-repo", "other-number"],
                [
                    TARGET._replace(head=OTHER),
                    TARGET._replace(base="develop"),
                    TARGET._replace(repo="o/other"),
                    TARGET._replace(number=8),
                ],
                strict=True,
            )
        ],
        scenes.scene(
            _judgment_ignores_rows_without_production_and_compares_repo_case_insensitively,
            id="judgment_ignores_rows_without_production_and_compares_repo_case_insensitively",
        ),
        scenes.scene(_write_merge_row_posts_once_per_record, id="write_merge_row_posts_once_per_record"),
        *[
            scenes.scene(
                _write_merge_row_refuses_bad_values,
                id=scenes.label("write_merge_row_refuses_bad_values", i, v),
                **dict(zip(("answer", "by"), v)),
            )
            for i, v in enumerate([("approved", "conductor"), ("maybe", "user")])
        ],
        scenes.scene(_release_check_does_not_count_merge_rows, id="release_check_does_not_count_merge_rows"),
    ],
)
def test_merge_row_scenes(request, scene, values):
    """マージの承認の証跡の読み書きと判定の場面（#4409 の決定 7）。"""
    scenes.play(request, scene, values)
