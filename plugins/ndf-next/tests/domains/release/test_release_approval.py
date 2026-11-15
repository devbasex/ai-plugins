"""承認の証跡（判定・答えの書き込みと読み戻し）と、証跡の後のマージ。"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ndf.adapters import ci
from ndf.adapters.github import Authored, GitHubError, MergeOutcome
from ndf.domains.release import approval_decide, declaration, land, trail

from ._fakes import HEAD, OTHER, FakeHub, not_found, pull


def _trail(answer: str, sha: str, author: str = "octocat") -> Authored:
    return Authored(author, "OWNER", approval_decide.render_trail(answer, sha, trail.USER_BY))


def test_judge_uses_latest_trusted_answer_on_head() -> None:
    comments = [
        ("octocat", approval_decide.render_trail("approved", HEAD, "x"), True),
        ("octocat", approval_decide.render_trail("rejected", HEAD, "x"), True),
        ("mallory", approval_decide.render_trail("approved", HEAD, "x"), False),
        ("octocat", approval_decide.render_request("資料", HEAD), True),
    ]
    got = approval_decide.judge(approval_decide.trails(comments), HEAD)
    assert got.state == approval_decide.REJECTED and got.trail is not None and got.trail.order == 1


def test_judge_reports_stale_and_missing() -> None:
    stale = approval_decide.judge(approval_decide.trails([("octocat", approval_decide.render_trail("approved", OTHER, "x"), True)]), HEAD)
    assert stale.state == approval_decide.STALE and not stale.approved
    assert approval_decide.judge([], HEAD).state == approval_decide.MISSING
    with pytest.raises(ValueError):
        approval_decide.render_trail("maybe", HEAD, "x")


def test_known_bug_answer_after_reapproval_reaches_the_record() -> None:
    """リリース 8 の事例: 先端が動いた後の承認し直しの答えが、今の先端への承認として判定に届く。"""
    hub = FakeHub(pulls={7: pull(head=HEAD)}, comments_of={7: [_trail("approved", OTHER)]})
    assert trail.read(hub, 7).judgment.state == approval_decide.STALE
    got = trail.answer(hub, 7, "approved", "人", presented=HEAD)
    assert got.approved and trail.read(hub, 7).judgment.approved


def test_answer_is_refused_when_it_does_not_reach_the_record() -> None:
    """書いた答えが読み直しで見えなければ、答えたつもりで進めずに止める。"""
    hub = FakeHub(pulls={7: pull(head=HEAD)}, drop_comments=True)
    with pytest.raises(trail.TrailError, match="届いていない"):
        trail.answer(hub, 7, "approved", "人", presented=HEAD)


def test_answer_is_refused_when_head_moved_since_presented() -> None:
    hub = FakeHub(pulls={7: pull(head=HEAD)})
    with pytest.raises(trail.TrailError, match="動いた"):
        trail.answer(hub, 7, "approved", "人", presented=OTHER)
    assert hub.comments(7) == []


def test_present_refuses_material_for_another_commit() -> None:
    """資料が対象にしたコミットが今の先端でなければ提示しない。先端と同じなら提示して先端を返す。"""
    for shown in (OTHER, None):
        hub = FakeHub(pulls={7: pull(head=HEAD)})
        material = "# 資料\n" if shown is None else f"{approval_decide.HEAD_LINE} `{shown}`\n"
        with pytest.raises(trail.TrailError):
            trail.present(hub, 7, material)
        assert hub.comments_of == {}
    hub = FakeHub(pulls={7: pull(head=HEAD)})
    assert trail.present(hub, 7, f"{approval_decide.HEAD_LINE} `{HEAD}`\n") == HEAD
    assert approval_decide.REQUEST_MARK in hub.comments_of[7][0].body


def test_check_posts_status_on_head() -> None:
    hub = FakeHub(pulls={7: pull(head=HEAD)}, comments_of={7: [_trail("approved", HEAD)]})
    trail.check(hub, 7)
    method, path, payload = hub.requests[-1]
    assert (method, path) == ("POST", f"/repos/o/r/statuses/{HEAD}")
    assert payload["state"] == "success" and payload["context"] == approval_decide.STATUS_CONTEXT


def _reading(failed: bool) -> ci.Reading:
    bad = [ci.Check("pytest", "done", "failure", 1)] if failed else []
    return ci.Reading(bad, [], [], [], [], [], True)


def _wait(failed: bool, calls: list[int]) -> Callable[[Callable[[], ci.Reading], float], ci.WatchResult]:
    def wait(reader: Callable[[], ci.Reading], budget: float) -> ci.WatchResult:
        calls.append(1)
        return ci.WatchResult(_reading(failed), 1, 0.0, 0.0)

    return wait


def _lander(hub: FakeHub, failed: bool, calls: list[int]) -> land.Lander:
    hub.responses[("GET", "/repos/o/r/rules/branches/main")] = [
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "pytest"}, {"context": approval_decide.STATUS_CONTEXT}]},
        }
    ]
    hub.responses[("GET", "/repos/o/r/rules/branches/develop")] = []
    return land.Lander(hub, "develop", "main", 60.0, wait=_wait(failed, calls))


def test_production_merge_needs_approval() -> None:
    hub = FakeHub(pulls={7: pull()})
    done = _lander(hub, False, []).land(7)
    assert not done.merged and done.kind == "refused" and hub.merged == []


def test_issue_1983_failed_ci_stops_without_retry() -> None:
    """#1983: 必須のチェックが落ちたら、打ち直さずに 1 回で止めて人へ戻す（マージしない）。"""
    calls: list[int] = []
    hub = FakeHub(pulls={7: pull()}, comments_of={7: [_trail("approved", HEAD)]})
    done = _lander(hub, True, calls).land(7)
    assert not done.merged and done.kind == "test_failed" and calls == [1] and hub.merged == []


def test_production_merge_rechecks_approval_after_waiting() -> None:
    """必須のチェックを待つ間に差し戻しが足されたら、マージしない（待ちの後に証跡を読み直す）。"""
    hub = FakeHub(pulls={7: pull()}, comments_of={7: [_trail("approved", HEAD)]})

    def wait(reader: Callable[[], ci.Reading], budget: float) -> ci.WatchResult:
        hub.comments_of[7].append(_trail("rejected", HEAD))
        return ci.WatchResult(_reading(False), 1, 0.0, 0.0)

    lander = _lander(hub, False, [])
    done = land.Lander(hub, lander.base, lander.production, lander.budget, wait=wait).land(7)
    assert not done.merged and done.kind == "refused" and hub.merged == []


def test_production_merge_then_sync_back_to_base() -> None:
    hub = FakeHub(pulls={7: pull(), 8: pull(number=8, base="develop")}, comments_of={7: [_trail("approved", HEAD)]})
    hub.responses[("POST", "/repos/o/r/pulls")] = {"number": 8}
    done = _lander(hub, False, []).land(7)
    assert done.merged and done.sync_pr == 8 and hub.merged == [7, 8]
    # 本番ブランチとベースブランチの祖先の関係を保つため、どちらも merge commit で入れる
    assert hub.merge_methods == [land.MERGE_METHOD, land.MERGE_METHOD] == ["merge", "merge"]
    assert land.Lander(hub, "develop", "main", 1.0).required("main") == ["pytest"]


def test_required_includes_classic_branch_protection() -> None:
    """ruleset に加えて従来の保護（branch protection）の必須のチェックも待つ。"""
    hub = FakeHub()
    hub.responses[("GET", "/repos/o/r/branches/main")] = {
        "protection": {"required_status_checks": {"contexts": ["lint"], "checks": [{"context": "build"}]}}
    }
    assert land.Lander(hub, "develop", "main", 1.0).required("main") == ["build", "lint"]


def test_commit_status_counts_as_required_check() -> None:
    """必須のチェックが外部の CI の commit status でも、通っていれば missing にしない。"""
    readers: list[Callable[[], ci.Reading]] = []

    def wait(reader: Callable[[], ci.Reading], budget: float) -> ci.WatchResult:
        readers.append(reader)
        return ci.WatchResult(reader(), 1, 0.0, 0.0)

    hub = FakeHub(pulls={8: pull(number=8, base="develop")})
    hub.responses[("GET", "/repos/o/r/branches/develop")] = {"protection": {"required_status_checks": {"contexts": ["ext-ci"]}}}
    hub.responses[("GET", f"/repos/o/r/commits/{HEAD}/status")] = {
        "statuses": [{"context": "ext-ci", "state": "success", "updated_at": "t1", "created_at": "t0", "id": 3}]
    }
    done = land.Lander(hub, "develop", "main", 1.0, wait=wait).land(8)
    reading = readers[0]()
    assert reading.missing == [] and reading.running == [] and done.merged and hub.merged == [8]


def test_sync_pr_uses_production_branch_as_head() -> None:
    """戻す PR は本番ブランチを head にして作る（リリースのブランチはマージの後に消えることがある）。"""
    hub = FakeHub(pulls={7: pull(), 8: pull(number=8, base="develop")}, comments_of={7: [_trail("approved", HEAD)]})
    hub.responses[("POST", "/repos/o/r/pulls")] = {"number": 8}
    _lander(hub, False, []).land(7)
    made = [payload for method, path, payload in hub.requests if (method, path) == ("POST", "/repos/o/r/pulls")]
    assert [(m["head"], m["base"]) for m in made] == [("main", "develop")]


def test_merged_release_resumes_sync_with_open_pr() -> None:
    """本番 PR がマージ済みでも、開いている戻す PR を使い直してマージを再開する。"""
    hub = FakeHub(pulls={7: pull(merged=True), 9: pull(number=9, base="develop")})
    hub.responses[("GET", "/repos/o/r/pulls")] = [{"number": 9}]
    done = _lander(hub, False, []).land(7)
    assert done.merged and done.sync_pr == 9 and hub.merged == [9]
    assert not any((m, p) == ("POST", "/repos/o/r/pulls") for m, p, _ in hub.requests)


def test_sync_without_diff_is_done_but_other_422_raises() -> None:
    """差分が無い 422 だけを「戻す差分なし」とし、ほかの 422 は隠さない。"""
    hub = FakeHub(pulls={7: pull(merged=True)})
    hub.responses[("POST", "/repos/o/r/pulls")] = GitHubError("No commits between develop and main", 422)
    done = _lander(hub, False, []).land(7)
    assert done.merged and done.sync_pr is None and hub.merged == []
    hub.responses[("POST", "/repos/o/r/pulls")] = GitHubError("Validation Failed: head invalid", 422)
    with pytest.raises(GitHubError):
        _lander(hub, False, []).land(7)


# --- #3506: 迂回の宣言（production_merge.admin）のある本番のマージ ------------------------------------

REVIEW_RULE = {"type": "pull_request", "parameters": {"required_approving_review_count": 1}}


@dataclass
class AdminHub(FakeHub):
    """迂回のマージ（`merge_as_admin`）を数える偽物。`fail_record` なら迂回の記録のコメントを断る。"""

    admin_merged: list[int] = field(default_factory=list)
    fail_record: bool = False

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> Any:
        if self.fail_record and method == "POST" and path.endswith("/comments") and trail.ADMIN_MARK in payload["body"]:
            raise GitHubError("書けない", 403)
        return super().rest(method, path, payload, params)

    def merge_as_admin(self, number: int, expect_head: str, method: str | None = None) -> MergeOutcome:
        self.admin_merged.append(number)
        self.merge_methods.append(method)
        return MergeOutcome(True, None, f"#{number} を迂回でマージした", self.pulls[number].head_sha)


def _admin_lander(hub: FakeHub, admin: bool = True, approvals: int = 1, failed: bool = False, wait: Any = None) -> land.Lander:
    base = _lander(hub, failed, [])
    rules = hub.responses[("GET", "/repos/o/r/rules/branches/main")]
    hub.responses[("GET", "/repos/o/r/rules/branches/main")] = [*rules, *([REVIEW_RULE] if approvals else [])]
    hub.responses[("GET", "/repos/o/r/branches/main/protection/required_pull_request_reviews")] = not_found()
    hub.responses[("POST", "/repos/o/r/pulls")] = {"number": 8}
    return land.Lander(hub, base.base, base.production, base.budget, wait=wait if wait is not None else base.wait, admin=admin)


def _mvv_trail(sha: str = HEAD) -> Authored:
    return Authored("octocat", "OWNER", approval_decide.render_trail("approved", sha, trail.MVV_BY))


def _records(hub: FakeHub, number: int = 7) -> list[str]:
    return [c.body for c in hub.comments_of.get(number, []) if trail.ADMIN_MARK in c.body]


def test_admin_declaration_merges_the_production_pr_with_the_bypass_and_records_it() -> None:
    """受け入れ条件 8・I7・I8: 宣言があり承認数 1 なら、迂回でマージし、記録を置き、admin が真。戻す PR は迂回しない。"""
    hub = AdminHub(pulls={7: pull(), 8: pull(number=8, base="develop")}, comments_of={7: [_trail("approved", HEAD)]})
    done = _admin_lander(hub).land(7)
    assert done.merged and done.admin and done.admin_record == "https://example.invalid/7" and done.record_error is None
    assert hub.admin_merged == [7] and hub.merged == [8] and hub.merge_methods == ["merge", "merge"]
    (record,) = _records(hub)
    assert HEAD in record and approval_decide.TRAIL_MARK not in record


def test_admin_declaration_stops_like_the_plain_merge_without_merging() -> None:
    """受け入れ条件 8（条件 2〜4）: 証跡が無い・チェックが落ちた・待つ間に差し戻されたら止まり、迂回しない。"""
    hub = AdminHub(pulls={7: pull()})
    done = _admin_lander(hub).land(7)
    assert not done.merged and done.kind == "refused" and hub.admin_merged == [] and not done.admin
    hub = AdminHub(pulls={7: pull()}, comments_of={7: [_trail("approved", HEAD)]})
    done = _admin_lander(hub, failed=True).land(7)
    assert not done.merged and done.kind == "test_failed" and hub.admin_merged == []
    hub = AdminHub(pulls={7: pull()}, comments_of={7: [_trail("approved", HEAD)]})

    def wait(reader: Callable[[], ci.Reading], budget: float) -> ci.WatchResult:
        hub.comments_of[7].append(_trail("rejected", HEAD))
        return ci.WatchResult(_reading(False), 1, 0.0, 0.0)

    done = _admin_lander(hub, wait=wait).land(7)
    assert not done.merged and done.kind == "refused" and hub.admin_merged == [] and _records(hub) == []


def test_admin_declaration_does_not_count_the_mvv_trail() -> None:
    """I9: 利用者の証跡が無く MVV 判定の証跡だけなら refused で止まり、迂回しない。"""
    hub = AdminHub(pulls={7: pull()}, comments_of={7: [_mvv_trail()]})
    done = _admin_lander(hub).land(7)
    assert not done.merged and done.kind == "refused" and hub.admin_merged == [] and hub.merged == []


@pytest.mark.parametrize(
    "rejection",
    [
        Authored("octocat", "OWNER", approval_decide.render_trail("rejected", HEAD, trail.MVV_BY)),  # MVV 判定の差し戻し
        Authored("octocat", "OWNER", approval_decide.render_trail("rejected", HEAD, trail.USER_BY).replace("担い手: user\n", "")),
    ],
)
def test_admin_declaration_counts_rejections_by_any_trusted_author(rejection: Authored) -> None:
    """I9: 利用者の承認の後の差し戻しは、担い手が user でなくても数えて refused で止まり、迂回しない。"""
    hub = AdminHub(pulls={7: pull()}, comments_of={7: [_trail("approved", HEAD), rejection]})
    done = _admin_lander(hub).land(7)
    assert not done.merged and done.kind == "refused" and hub.admin_merged == [] and hub.merged == []


@pytest.mark.parametrize(
    "comment",
    [
        Authored("octocat", "OWNER", approval_decide.render_trail("approved", HEAD, "x")),  # 担い手が user でない
        Authored("octocat", "OWNER", approval_decide.render_trail("approved", HEAD, trail.USER_BY).replace("担い手: user\n", "")),
        Authored("octocat", "OWNER", approval_decide.render_trail("approved", HEAD, trail.USER_BY), edited=True),  # 編集された
    ],
)
def test_admin_declaration_counts_only_unedited_user_trails(comment: Authored) -> None:
    """I9: 担い手が user と明示されていない・編集された証跡では、迂回の前提に数えず refused で止まる。"""
    hub = AdminHub(pulls={7: pull()}, comments_of={7: [comment]})
    done = _admin_lander(hub).land(7)
    assert not done.merged and done.kind == "refused" and hub.admin_merged == [] and hub.merged == []


def test_admin_record_ignores_records_by_untrusted_or_edited_comments() -> None:
    """I7: 信用しない書き手か編集された、印と先端の SHA を持つコメントがあっても、本物の記録を書く。"""
    fake = trail.render_admin_record(HEAD, "")
    hub = AdminHub(
        pulls={7: pull()},
        comments_of={7: [_trail("approved", HEAD), Authored("mallory", "NONE", fake), Authored("octocat", "OWNER", fake, edited=True)]},
    )
    wrote, url = trail.write_admin_record(hub, 7, HEAD)
    assert wrote and url == "https://example.invalid/7" and len(_records(hub)) == 3
    assert trail.write_admin_record(hub, 7, HEAD)[0] is False


def test_authored_from_rest_marks_edited_comments() -> None:
    raw = {"user": {"login": "u"}, "author_association": "owner", "body": "b", "created_at": "t1", "updated_at": "t1"}
    assert not Authored.from_rest(raw).edited
    assert Authored.from_rest({**raw, "updated_at": "t2"}).edited


@pytest.mark.parametrize("admin, approvals", [(False, 1), (True, 0)])
def test_without_the_bypass_the_merge_stays_as_it_was(admin: bool, approvals: int) -> None:
    """受け入れ条件 8（迂回しない）・I2b: 宣言が無い・承認数 0 なら今のマージで、記録を置かず admin は偽。"""
    hub = AdminHub(pulls={7: pull(), 8: pull(number=8, base="develop")}, comments_of={7: [_trail("approved", HEAD)]})
    done = _admin_lander(hub, admin=admin, approvals=approvals).land(7)
    assert done.merged and not done.admin and done.admin_record is None
    assert hub.admin_merged == [] and hub.merged == [7, 8] and _records(hub) == []


def test_approvals_reads_rules_then_protection_and_none_when_unreadable() -> None:
    hub = FakeHub()
    lander = land.Lander(hub, "develop", "main", 1.0, admin=True)
    hub.responses[("GET", "/repos/o/r/rules/branches/main")] = [REVIEW_RULE]
    assert lander.approvals("main") == 1
    hub.responses[("GET", "/repos/o/r/rules/branches/main")] = []
    hub.responses[("GET", "/repos/o/r/branches/main/protection/required_pull_request_reviews")] = {"required_approving_review_count": 2}
    assert lander.approvals("main") == 2
    hub.responses[("GET", "/repos/o/r/branches/main/protection/required_pull_request_reviews")] = not_found()
    assert lander.approvals("main") == 0
    hub.responses[("GET", "/repos/o/r/rules/branches/main")] = GitHubError("壊れた", 500)
    assert lander.approvals("main") is None and not lander._bypass(pull())


def test_merged_admin_release_resume_writes_the_missing_record_once() -> None:
    """I7: マージ済みの本番の PR の打ち直しは、利用者の証跡がマージした先端と一致し記録が無いときだけ書く。何度打っても 1 件。"""
    hub = AdminHub(pulls={7: pull(merged=True), 9: pull(number=9, base="develop")}, comments_of={7: [_trail("approved", HEAD)]})
    hub.responses[("GET", "/repos/o/r/pulls")] = [{"number": 9}]
    first = _admin_lander(hub).land(7)
    second = _admin_lander(hub).land(7)
    assert first.admin and first.admin_record and second.admin and len(_records(hub)) == 1 and hub.admin_merged == []
    hub = AdminHub(pulls={7: pull(merged=True), 9: pull(number=9, base="develop")}, comments_of={7: [_trail("approved", OTHER)]})
    hub.responses[("GET", "/repos/o/r/pulls")] = [{"number": 9}]
    done = _admin_lander(hub).land(7)
    assert not done.admin and _records(hub) == []


def test_admin_record_failure_is_reported_apart_from_the_merge() -> None:
    """決定 7: 記録を書けなければ、マージは済んだまま record_error に理由を残す。"""
    hub = AdminHub(pulls={7: pull(), 8: pull(number=8, base="develop")}, comments_of={7: [_trail("approved", HEAD)]}, fail_record=True)
    done = _admin_lander(hub).land(7)
    assert done.merged and done.admin and done.admin_record is None and done.record_error and hub.admin_merged == [7]


@pytest.mark.parametrize(
    "body, admin",
    [
        ({"production_merge": {"admin": True}}, True),
        ({"production_merge": {"admin": "true"}}, False),
        ({"production_merge": {"unknown": "未定"}}, False),
        ({}, False),
        (None, False),
        ("{", False),
    ],
)
def test_production_admin_reads_only_json_true(tmp_path: Path, body: Any, admin: bool) -> None:
    path = tmp_path / declaration.PROJECT_FILE
    if body is not None:
        path.parent.mkdir(parents=True)
        path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    assert declaration.production_admin(tmp_path) is admin


def test_production_admin_reads_the_main_directory_before_the_worktree(tmp_path: Path) -> None:
    """I1: PR の作業場所の宣言だけに迂回を足しても選べない。メインディレクトリの宣言だけが効く。"""
    import subprocess

    main, wt = tmp_path / "main", tmp_path / "wt"

    def git(*args: str, cwd: Path = main) -> None:
        subprocess.run(["git", "-c", "user.email=t@e", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True)

    main.mkdir()
    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "init")
    git("worktree", "add", "-q", "--detach", str(wt))
    for root, admin in ((main, False), (wt, True)):
        (root / declaration.PROJECT_FILE).parent.mkdir(parents=True)
        (root / declaration.PROJECT_FILE).write_text(json.dumps({"production_merge": {"admin": admin}}), encoding="utf-8")
    assert declaration.production_admin(wt) is False
    (main / declaration.PROJECT_FILE).unlink()
    assert declaration.production_admin(wt) is False  # メインディレクトリに無ければ作業場所の宣言で補わない（#2363）
