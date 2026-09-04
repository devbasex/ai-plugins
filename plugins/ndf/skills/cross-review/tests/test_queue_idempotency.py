"""待ち行列を 2 度流しても、GitHub 側に 2 件目が作られない（#291、受け入れ条件 8）。

上限のときに積んだ項目は、流すきっかけが自動と明示の 2 つあるため、同じ項目を 2 度
流そうとすることがある。**流す前に、同じ内容が既に GitHub 側にあるかを種別ごとの照会で
確かめる。**

| 種別 | 照会 | 同じとみなす条件 |
| --- | --- | --- |
| `pr-comment` | `repos/{リポジトリ}/issues/{番号}/comments` | 投稿者が自分で、本文が一致 |
| `review-post` | `repos/{リポジトリ}/pulls/{番号}/reviews` | 投稿者・判定・本文の先頭 80 文字が一致 |
| `review-reply` | `repos/{リポジトリ}/pulls/{番号}/comments` | `in_reply_to_id` と本文が一致 |
| `thread-resolve` | 未解決のスレッドの一覧 | 識別子が一覧に無い |

本文の先頭 80 文字で比べるのは、振動の検知が指摘の同一性を測るときと同じ幅である。
**同じ判断に別々の値を持たない。**
"""
from __future__ import annotations

import json
import pathlib

import pytest

REPO = "o/r"
PR = 291
ACTOR = "takemi"
BODY = "同じ内容の本文。" * 12   # 80 文字より長い本文で、先頭の照合が効くことを見る


@pytest.fixture()
def qdir(tmp_path) -> pathlib.Path:
    return tmp_path / "pending"


def _posted(calls: list[str]) -> list[str]:
    """状態を変える呼び出しだけを取り出す。"""
    return [c for c in calls if "--method POST" in c or "resolveReviewThread" in c]


def test_the_widths_of_the_body_comparison_are_the_same(queue_mod, state_mod) -> None:
    """冪等の照合と振動の検知は、同じ幅で本文を比べる。別々に決めない。"""
    assert queue_mod.BODY_MATCH_CHARS == state_mod.OSCILLATION_BODY_CHARS


def test_a_pr_comment_already_on_github_is_not_posted_again(
        queue_mod, fake_gh, qdir) -> None:
    q = queue_mod.Queue(qdir)
    queue_mod.enqueue(q, "pr-comment", REPO, PR, {"body": BODY}, actor=ACTOR)
    fake_gh.set_rules([
        {"match": f"issues/{PR}/comments",
         "stdout": json.dumps([{"user": {"login": ACTOR}, "body": BODY}])},
    ])

    result = q.flush()

    assert len(result.skipped) == 1 and result.sent == []
    assert q.count() == 0
    assert _posted(fake_gh.joined()) == []


def test_a_pr_comment_by_someone_else_is_not_treated_as_the_same(
        queue_mod, fake_gh, qdir) -> None:
    """本文が同じでも投稿者が違えば、こちらの投稿はまだ届いていない。"""
    q = queue_mod.Queue(qdir)
    queue_mod.enqueue(q, "pr-comment", REPO, PR, {"body": BODY}, actor=ACTOR)
    fake_gh.set_rules([
        {"match": f"issues/{PR}/comments?",
         "stdout": json.dumps([{"user": {"login": "someone"}, "body": BODY}])},
        {"match": "", "stdout": "{}"},
    ])

    result = q.flush()

    assert len(result.sent) == 1
    assert _posted(fake_gh.joined())


def test_a_review_already_on_github_is_not_posted_again(
        queue_mod, fake_gh, qdir) -> None:
    q = queue_mod.Queue(qdir)
    queue_mod.enqueue(
        q, "review-post", REPO, PR,
        {"body": BODY, "event": "REQUEST_CHANGES"}, actor=ACTOR)
    # 末尾だけが違う本文でも、先頭 80 文字が同じなら同じ投稿とみなす。
    fake_gh.set_rules([
        {"match": f"pulls/{PR}/reviews",
         "stdout": json.dumps([{"user": {"login": ACTOR}, "state": "CHANGES_REQUESTED",
                                "body": BODY + "（末尾の言い回しだけが違う）",
                                "id": 4961230016,
                                "html_url": "https://x/#pullrequestreview-4961230016"}])},
    ])

    result = q.flush()

    assert len(result.skipped) == 1
    assert _posted(fake_gh.joined()) == []
    # **送った場合と同じ形で返す。** 呼び出し側は届いたことを応答から確かめるため、
    # 照会で見つけた投稿を応答の代わりに積む。無いと `queued` を解除できない。
    assert result.skipped[0]["response"]["html_url"] == \
        "https://x/#pullrequestreview-4961230016"


def test_a_review_with_a_different_verdict_is_not_the_same(
        queue_mod, fake_gh, qdir) -> None:
    q = queue_mod.Queue(qdir)
    queue_mod.enqueue(
        q, "review-post", REPO, PR,
        {"body": BODY, "event": "REQUEST_CHANGES"}, actor=ACTOR)
    fake_gh.set_rules([
        {"match": f"pulls/{PR}/reviews?",
         "stdout": json.dumps([{"user": {"login": ACTOR}, "state": "APPROVED",
                                "body": BODY}])},
        {"match": "", "stdout": "{}"},
    ])

    result = q.flush()

    assert len(result.sent) == 1


def test_a_review_reply_already_on_github_is_not_posted_again(
        queue_mod, fake_gh, qdir) -> None:
    q = queue_mod.Queue(qdir)
    queue_mod.enqueue(
        q, "review-reply", REPO, PR,
        {"body": BODY, "in_reply_to": 987654}, actor=ACTOR)
    fake_gh.set_rules([
        {"match": f"pulls/{PR}/comments",
         "stdout": json.dumps([{"user": {"login": ACTOR}, "body": BODY,
                                "in_reply_to_id": 987654}])},
    ])

    result = q.flush()

    assert len(result.skipped) == 1
    assert _posted(fake_gh.joined()) == []


def test_a_resolved_thread_is_not_resolved_again(queue_mod, fake_gh, qdir) -> None:
    """未解決の一覧に無ければ、既に解決されている。"""
    q = queue_mod.Queue(qdir)
    queue_mod.enqueue(q, "thread-resolve", REPO, PR, {"thread_id": "PRRT_kwABC"})
    fake_gh.set_rules([
        {"match": "graphql", "stdout": "PRRT_kwOTHER\n"},
    ])

    result = q.flush()

    assert len(result.skipped) == 1
    assert _posted(fake_gh.joined()) == []


def test_an_unresolved_thread_is_still_resolved(queue_mod, fake_gh, qdir) -> None:
    q = queue_mod.Queue(qdir)
    queue_mod.enqueue(q, "thread-resolve", REPO, PR, {"thread_id": "PRRT_kwABC"})
    fake_gh.set_rules([
        {"match": "reviewThreads", "stdout": "PRRT_kwABC\nPRRT_kwOTHER\n"},
        {"match": "", "stdout": "{}"},
    ])

    result = q.flush()

    assert len(result.sent) == 1
    assert any("resolveReviewThread" in c for c in fake_gh.joined())


def test_flushing_twice_posts_once(queue_mod, fake_gh, qdir) -> None:
    """2 度流しても 2 件目が作られないことを、通しで見る。"""
    q = queue_mod.Queue(qdir)
    queue_mod.enqueue(q, "pr-comment", REPO, PR, {"body": BODY}, actor=ACTOR)
    fake_gh.set_rules([
        {"match": f"issues/{PR}/comments?", "stdout": "[]"},
        {"match": "", "stdout": "{}"},
    ])
    q.flush()
    assert len(_posted(fake_gh.joined())) == 1

    # 2 度目: 同じ内容をもう一度積み、GitHub 側には 1 件目がある。
    queue_mod.enqueue(q, "pr-comment", REPO, PR, {"body": BODY}, actor=ACTOR)
    fake_gh.set_rules([
        {"match": f"issues/{PR}/comments?",
         "stdout": json.dumps([{"user": {"login": ACTOR}, "body": BODY}])},
        {"match": "", "stdout": "{}"},
    ])
    q.flush()

    assert len(_posted(fake_gh.joined())) == 1
