"""承認の証跡の判定と、証跡のチェック・開発版の 2 つの workflow（#2773）。

- 証跡は印と `承認したコミット:` の 40 桁を 1 つだけ持つコメントで、承認資料のコメントは数えない（受け入れ条件 3・6）
- 承認した人と時刻は作成者と作成の時刻で読み、本文の記述と編集された証跡を判定に使わない（受け入れ条件 4・10・I4）
- 証跡と先端が違えば両方の短い SHA を出して落ちる（受け入れ条件 5・I1）
- 承認権限は role_name の admin・maintain だけで、読めない人は数えない（受け入れ条件 7・I3）
- 場合 2 のレビューの承認は permission が admin・write の人の最新の APPROVED だけを読む（I8）
- 証跡のチェックの workflow は既定ブランチの定義で動き、PR のコードを取り出さず、秘密を使わない（受け入れ条件 9・21・I5）
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import gh_call  # noqa: E402
from release_lib import approval_trail as at  # noqa: E402

ROOT = SCRIPTS.parents[2]
A = "a" * 40
B = "b" * 40


def comment(body, login="admin1", created="2026-11-13T09:00:00Z", updated=None, cid=1):
    return {"id": cid, "body": body, "user": {"login": login}, "created_at": created, "updated_at": updated or created}


def test_parse_trail_reads_one_sha_and_rejects_request_and_counts():
    assert at.parse_trail(at.render_trail(A, "user")) == A
    assert at.parse_trail(at.render_trail(A, "mvv")) == A
    upper = at.render_trail(A, "user").replace(A, A.upper())
    assert at.parse_trail(upper) == A
    assert at.parse_trail(f"{at.TRAIL_MARK}\n  承認したコミット:  {A}  \n") == A  # 前後の空白・バッククォートの無し
    assert at.parse_trail(f"承認したコミット: `{A}`") is None  # 印が無い
    assert at.parse_trail(at.render_request(f"承認したコミット: `{A}`", A)) is None  # 承認資料
    assert at.parse_trail(f"{at.TRAIL_MARK}\n承認したコミット: `{A[:39]}`") is None  # 39 桁
    assert at.parse_trail(f"{at.TRAIL_MARK}\n承認したコミット: `{A}` `{B}`") is None  # 2 つ
    assert at.parse_trail(f"{at.TRAIL_MARK}\n本文だけ") is None


def test_trails_of_marks_edited_and_orders_by_creation():
    cs = [
        comment(at.render_trail(B, "user"), created="2026-11-13T10:00:00Z", cid=2),
        comment("無関係", cid=3),
        comment(at.render_request("x", A), cid=4),
        comment(at.render_trail(A, "user"), created="2026-11-13T09:00:00Z", updated="2026-11-13T09:00:01Z", cid=1),
    ]
    got = at.trails_of(cs)
    assert [(t.sha, t.edited, t.comment_id) for t in got] == [(A, True, 1), (B, False, 2)]


@pytest.mark.parametrize(
    "trails, roles, state, desc",
    [
        ([], {}, "failure", "証跡が無い"),
        ([at.Trail(A, "x", "t1", True, 1)], {"x": "admin"}, "failure", "編集された証跡だけがある"),
        ([at.Trail(A, "w", "t1", False, 1)], {"w": "write"}, "failure", "承認権限の無い w の証跡だけがある"),
        ([at.Trail(A, "t", "t1", False, 1)], {"t": "triage"}, "failure", "承認権限の無い t の証跡だけがある"),
        ([at.Trail(A, "u", "t1", False, 1)], {"u": None}, "failure", "権限を読めない u"),
    ],
)
def test_judge_failures(trails, roles, state, desc):
    j = at.judge(trails, roles, A)
    assert (j.state, j.description) == (state, desc)


def test_judge_success_reads_author_and_creation_time_not_body():
    t = at.Trail(A, "boss", "2026-11-13T09:00:00Z", False, 1)
    for role in ("admin", "maintain"):
        j = at.judge([t], {"boss": role}, A)
        assert (j.state, j.approver, j.at) == ("success", "boss", "2026-11-13T09:00:00Z")
        assert j.description == "承認: boss 2026-11-13T09:00:00Z"


def test_judge_uses_latest_valid_trail_and_reports_both_shas():
    old = at.Trail(A, "boss", "2026-11-13T09:00:00Z", False, 1)
    new = at.Trail(B, "boss", "2026-11-13T10:00:00Z", False, 2)
    j = at.judge([old, new], {"boss": "admin"}, A)
    assert j.state == "failure" and j.description == f"証跡 {B[:7]} ≠ 先端 {A[:7]}"
    # 権限の無い人の新しい証跡は数えない
    stray = at.Trail(B, "w", "2026-11-13T11:00:00Z", False, 3)
    assert at.judge([old, stray], {"boss": "admin", "w": "write"}, A).state == "success"
    # 前方一致では通さない
    assert at.judge([old], {"boss": "admin"}, A[:39] + "b").state == "failure"


def test_judge_cuts_long_description():
    names = [at.Trail(A, f"user{i:03d}", "t", False, i) for i in range(40)]
    j = at.judge(names, {}, A)
    assert len(j.description) <= at.DESCRIPTION_LIMIT


def test_approved_review_commit_counts_only_effective_reviewers():
    def rv(login, state, sha, at_):
        return {"user": {"login": login}, "state": state, "commit_id": sha, "submitted_at": at_}

    reviews = [rv("w", "APPROVED", A, "1"), rv("w", "COMMENTED", B, "2"), rv("r", "APPROVED", B, "3"), rv("x", "APPROVED", B, "4")]
    perms = {"w": "write", "r": "read", "x": None}
    assert at.approved_review_commit(reviews, perms) == A
    assert at.approved_review_commit(reviews, {"w": "admin", "r": "write", "x": None}) == B
    assert at.approved_review_commit([rv("w", "COMMENTED", A, "1")], perms) is None
    assert at.approved_review_commit(reviews, {}) is None


def test_has_request_by_head():
    cs = [comment(at.render_request("資料", A)), comment(at.render_trail(B, "user"))]
    assert at.has_request(cs, A) and not at.has_request(cs, B)


class FakeGh:
    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, args, stdin=None, cwd=None):
        self.calls.append(args)
        for key, (code, out) in self.table.items():
            if key in " ".join(args):
                return gh_call.GhResult(code, out, "" if code == 0 else out)
        return gh_call.GhResult(1, "", "not stubbed")


def test_readers_join_pages_and_raise_unreadable(monkeypatch):
    rows = [comment("x", cid=1), comment("y", cid=2)]
    fake = FakeGh({"issues/5/comments": (0, "\n".join(json.dumps(r) for r in rows) + "\n")})
    monkeypatch.setattr(gh_call, "RUNNER", fake)
    assert [c["id"] for c in at.read_comments("o/r", 5)] == [1, 2]
    assert "--paginate" in fake.calls[0]
    monkeypatch.setattr(gh_call, "RUNNER", FakeGh({}))
    with pytest.raises(at.TrailError) as e:
        at.read_reviews("o/r", 5)
    assert e.value.kind == "unreadable"


def test_read_roles_reads_once_and_none_for_unreadable(monkeypatch):
    fake = FakeGh({"collaborators/boss/": (0, json.dumps({"permission": "write", "role_name": "maintain"}))})
    monkeypatch.setattr(gh_call, "RUNNER", fake)
    assert at.read_roles("o/r", ["boss", "boss", "ghost"]) == {"boss": "maintain", "ghost": None}
    assert at.read_permissions("o/r", ["boss"]) == {"boss": "write"}
    assert sum("collaborators/boss/permission" in " ".join(c) for c in fake.calls) == 2


def test_open_prs_at_and_post_status(monkeypatch):
    prs = [{"number": 9, "head": {"sha": A}}, {"number": 3, "head": {"sha": A}}, {"number": 4, "head": {"sha": B}}]
    fake = FakeGh({"pulls?state=open": (0, "\n".join(json.dumps(p) for p in prs)), "/statuses/": (0, "{}")})
    monkeypatch.setattr(gh_call, "RUNNER", fake)
    assert at.open_prs_at("o/r", A, "main") == [3, 9]
    at.post_status("o/r", A, at.Judgment("failure", "証跡が無い"))
    posted = fake.calls[-1]
    assert f"context={at.STATUS_CONTEXT}" in posted and "state=failure" in posted


def test_read_review_commit_reads_reviews_and_permissions_of_approvers(monkeypatch):
    def rv(login, state, sha, at_):
        return {"user": {"login": login}, "state": state, "commit_id": sha, "submitted_at": at_}

    reviews = [rv("w", "APPROVED", A, "1"), rv("c", "COMMENTED", B, "2"), rv("r", "APPROVED", B, "3")]
    fake = FakeGh(
        {
            "pulls/5/reviews": (0, "\n".join(json.dumps(r) for r in reviews)),
            "collaborators/w/": (0, json.dumps({"permission": "write"})),
            "collaborators/r/": (0, json.dumps({"permission": "read"})),
        }
    )
    monkeypatch.setattr(gh_call, "RUNNER", fake)
    assert at.read_review_commit("o/r", 5) == A
    assert not any("collaborators/c/" in " ".join(c) for c in fake.calls)  # 承認していない人の権限は読まない
    monkeypatch.setattr(gh_call, "RUNNER", FakeGh({"pulls/5/reviews": (0, "")}))
    assert at.read_review_commit("o/r", 5) is None


def test_judge_trails_on_reads_roles_of_unedited_trails_only(monkeypatch):
    cs = [
        comment(at.render_trail(A, "user"), login="boss", cid=1),
        comment(at.render_trail(A, "user"), login="old", updated="2026-11-14T00:00:00Z", cid=2),
    ]
    fake = FakeGh(
        {
            "issues/5/comments": (0, "\n".join(json.dumps(c) for c in cs)),
            "collaborators/boss/": (0, json.dumps({"role_name": "admin"})),
        }
    )
    monkeypatch.setattr(gh_call, "RUNNER", fake)
    got = at.judge_trails_on("o/r", 5, A)
    assert (got.state, got.approver) == ("success", "boss")
    assert not any("collaborators/old/" in " ".join(c) for c in fake.calls)
    assert at.judge_trails_on("o/r", 5, B).state == "failure"


def test_write_trail(monkeypatch):
    monkeypatch.setattr(at, "read_head", lambda repo, pr: (A, "main", "open"))
    monkeypatch.setattr(at.gh_rest, "viewer_login", lambda: "boss")
    posted = []

    class Ok:
        ok, error, value = True, "", {"url": "https://x/c/1"}

    monkeypatch.setattr(at.gh_rest, "comment", lambda repo, n, body: posted.append(body) or Ok())
    monkeypatch.setattr(at, "read_comments", lambda repo, pr: [])
    assert at.write_trail("o/r", 5, A, "user") == (True, "https://x/c/1")
    assert at.parse_trail(posted[0]) == A
    monkeypatch.setattr(at, "read_comments", lambda repo, pr: [comment(at.render_trail(A, "user"), login="boss")])
    assert at.write_trail("o/r", 5, A, "user")[0] is False and len(posted) == 1
    edited = comment(at.render_trail(A, "user"), login="boss", updated="2026-11-14T00:00:00Z")
    monkeypatch.setattr(at, "read_comments", lambda repo, pr: [edited])
    assert at.write_trail("o/r", 5, A, "user")[0] is True
    with pytest.raises(at.TrailError) as e:
        at.write_trail("o/r", 5, B, "user")
    assert e.value.kind == "head_moved"


def _workflow(name):
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def _triggers(text):
    block = text.split("\non:", 1)[1].split("\npermissions:", 1)[0]
    return set(re.findall(r"^  ([a-z_]+):", block, re.M))


def test_approval_workflow_runs_on_default_branch_definition_only():
    text = _workflow("release-approval.yml")
    assert _triggers(text) == {"pull_request_target", "issue_comment"}
    assert "github.event.pull_request.head" not in text and "head.sha" not in text  # PR の head を取り出さない
    assert "secrets." not in text
    assert "statuses: write" in text and "contents: write" not in text


def test_dev_workflow_uses_dispatch_and_token_only():
    text = _workflow("release-dev.yml")
    assert _triggers(text) == {"workflow_dispatch"}
    assert "secrets." not in text and "--no-admin" in text
