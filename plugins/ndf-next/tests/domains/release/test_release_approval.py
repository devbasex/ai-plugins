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
from ndf.domains.release import approval_decide, declaration, land, merge_decide, records_trail, required_checks, trail
from ndf.domains.release import cli as release_cli

from ._fakes import HEAD, OTHER, FakeHub, ci_reading, not_found, pull, release_lander, trail_comment


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
    hub = FakeHub(pulls={7: pull(head=HEAD)}, comments_of={7: [trail_comment("approved", OTHER)]})
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
    hub = FakeHub(pulls={7: pull(head=HEAD)}, comments_of={7: [trail_comment("approved", HEAD)]})
    trail.check(hub, 7)
    method, path, payload = hub.requests[-1]
    assert (method, path) == ("POST", f"/repos/o/r/statuses/{HEAD}")
    assert payload["state"] == "success" and payload["context"] == approval_decide.STATUS_CONTEXT


def test_production_merge_needs_approval() -> None:
    hub = FakeHub(pulls={7: pull()})
    done = release_lander(hub, False, []).land(7)
    assert not done.merged and done.kind == "refused" and hub.merged == []


def test_issue_1983_failed_ci_stops_without_retry() -> None:
    """#1983: 必須のチェックが落ちたら、打ち直さずに 1 回で止めて人へ戻す（マージしない）。"""
    calls: list[int] = []
    hub = FakeHub(pulls={7: pull()}, comments_of={7: [trail_comment("approved", HEAD)]})
    done = release_lander(hub, True, calls).land(7)
    assert not done.merged and done.kind == "test_failed" and calls == [1] and hub.merged == []


def test_production_merge_rechecks_approval_after_waiting() -> None:
    """必須のチェックを待つ間に差し戻しが足されたら、マージしない（待ちの後に証跡を読み直す）。"""
    hub = FakeHub(pulls={7: pull()}, comments_of={7: [trail_comment("approved", HEAD)]})

    def wait(reader: Callable[[], ci.Reading], budget: float) -> ci.WatchResult:
        hub.comments_of[7].append(trail_comment("rejected", HEAD))
        return ci.WatchResult(ci_reading(False), 1, 0.0, 0.0)

    lander = release_lander(hub, False, [])
    done = land.Lander(hub, lander.production, lander.budget, wait=wait).land(7)
    assert not done.merged and done.kind == "refused" and hub.merged == []


def test_production_merge_makes_no_sync_pr() -> None:
    """受け入れ条件 13・I10: 本番の PR（head は sprint/）をマージした後に、同期の PR も次の版のブランチも作らない。"""
    hub = FakeHub(pulls={7: pull(ref="sprint/lrel")}, comments_of={7: [trail_comment("approved", HEAD)]})
    done = release_lander(hub, False, []).land(7)
    assert done.merged and hub.merged == [7]
    assert not any(m == "POST" and (p.endswith("/pulls") or p.endswith("/git/refs")) for m, p, _ in hub.requests)
    # 版上げのコミットの範囲を保つため merge commit で入れ、キューの方式が違えば入れない
    assert hub.merge_methods == [land.MERGE_METHOD] == ["merge"] and hub.merge_required == [True]
    assert land.Lander(hub, "main", 1.0).required("main").names == ["pytest"]


def test_required_includes_classic_branch_protection() -> None:
    """ruleset に加えて従来の保護（branch protection）の必須のチェックも待つ。"""
    hub = FakeHub()
    hub.responses[("GET", "/repos/o/r/branches/main")] = {
        "protection": {"required_status_checks": {"contexts": ["lint"], "checks": [{"context": "build"}]}}
    }
    assert land.Lander(hub, "main", 1.0).required("main").names == ["build", "lint"]


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
    done = land.Lander(hub, "main", 1.0, wait=wait).land(8)
    reading = readers[0]()
    assert reading.missing == [] and reading.running == [] and done.merged and hub.merged == [8]


def _run(name: str, app: int, run_id: int = 1) -> dict:
    return {
        "name": name,
        "status": "completed",
        "conclusion": "success",
        "completed_at": "t1",
        "started_at": "t0",
        "id": run_id,
        "app": {"id": app},
    }


def test_required_check_with_a_named_app_counts_only_that_app() -> None:
    """#3506: 規則が発行元（ruleset の integration_id・従来の保護の app_id）を指定していれば、同じ名前でも別のアプリの
    check run と commit status を数えない（迂回で GitHub が拒むマージを通さない）。app_id の -1 は発行元を問わない。"""
    hub = FakeHub()
    hub.responses[("GET", "/repos/o/r/rules/branches/main")] = [
        {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "pytest", "integration_id": 15368}]}}
    ]
    hub.responses[("GET", "/repos/o/r/branches/main")] = {
        "protection": {"required_status_checks": {"checks": [{"context": "build", "app_id": 7}, {"context": "lint", "app_id": -1}]}}
    }
    lander = land.Lander(hub, "main", 1.0)
    assert lander.required("main") == required_checks.Required(
        ["build", "lint", "pytest"], {"pytest": frozenset({15368}), "build": frozenset({7})}
    )
    runs = [_run("pytest", 999, 1), _run("build", 7, 2), _run("lint", 42, 3)]
    hub.status_list = [_status("pytest", "someone", 4)]
    readers: list[Callable[[], ci.Reading]] = []

    def wait(reader: Callable[[], ci.Reading], budget: float) -> ci.WatchResult:
        readers.append(reader)
        return ci.WatchResult(reader(), 1, 0.0, 0.0)

    hub.runs = runs
    hub.pulls = {7: pull()}
    hub.comments_of = {7: [trail_comment("approved", HEAD)]}
    done = land.Lander(hub, "main", 1.0, wait=wait).land(7)
    assert readers[0]().missing == ["pytest"] and not done.merged and hub.merged == []
    hub.runs = [*runs, _run("pytest", 15368, 5)]
    assert readers[0]().missing == []


def _status(context: str, creator: str, status_id: int, state: str = "success") -> dict:
    return {"context": context, "state": state, "updated_at": "t1", "created_at": "t0", "id": status_id, "creator": {"login": creator}}


def _named_app_lander(hub: FakeHub) -> tuple[land.Lander, list[Callable[[], ci.Reading]]]:
    hub.responses[("GET", "/repos/o/r/rules/branches/main")] = [
        {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "pytest", "integration_id": 15368}]}}
    ]
    hub.pulls = {7: pull()}
    hub.comments_of = {7: [trail_comment("approved", HEAD)]}
    readers: list[Callable[[], ci.Reading]] = []

    def wait(reader: Callable[[], ci.Reading], budget: float) -> ci.WatchResult:
        readers.append(reader)
        return ci.WatchResult(reader(), 1, 0.0, 0.0)

    return land.Lander(hub, "main", 1.0, wait=wait), readers


def test_required_check_with_a_named_app_counts_that_apps_commit_status() -> None:
    """#3506: 発行元を指定された必須のチェックは、そのアプリの bot が付けた commit status でも満たす。別の書き手の status は数えない。"""
    hub = FakeHub(bot_apps={"github-actions[bot]": 15368, "other-app[bot]": 99})
    hub.status_list = [_status("pytest", "other-app[bot]", 1)]
    lander, readers = _named_app_lander(hub)
    assert not lander.land(7).merged and readers[0]().missing == ["pytest"]
    hub.status_list = [_status("pytest", "other-app[bot]", 1), _status("pytest", "github-actions[bot]", 2)]
    assert readers[0]().missing == [] and hub.merged == []


@pytest.mark.parametrize("name", ["other / pytest", "pytest (1/2)", "ci / pytest (0/2)"])
def test_named_app_check_is_matched_by_the_same_name_rule_as_the_wait(name: str) -> None:
    """#3506: 別の発行元の実行は、前置きや分割ジョブの末尾が付いていても、必須のチェックとして数えない（待ちと同じ照合で絞る）。"""
    hub = FakeHub(runs=[_run(name, 999, 1)])
    hub.status_list = [_status(name, "someone", 2)]
    lander, readers = _named_app_lander(hub)
    assert not lander.land(7).merged and readers[0]().missing == ["pytest"] and hub.merged == []
    hub.runs = [_run(name, 999, 1), _run(f"ci / {name.rpartition(' / ')[2]}", 15368, 3)]
    assert readers[0]().missing == []


def test_merged_production_pr_resumes_without_a_sync_pr() -> None:
    """I10: マージ済みの本番の PR から打ち直しても、同期の PR を作らずマージも呼ばない。"""
    hub = FakeHub(pulls={7: pull(merged=True)})
    done = release_lander(hub, False, []).land(7)
    assert done.merged and hub.merged == []
    assert not any((m, p) == ("POST", "/repos/o/r/pulls") for m, p, _ in hub.requests)


# --- #3506: 迂回の宣言（production_merge.admin）のある本番のマージ ------------------------------------

REVIEW_RULE = {"type": "pull_request", "parameters": {"required_approving_review_count": 1}}


@dataclass
class AdminHub(FakeHub):
    """迂回のマージ（`merge_as_admin`）を数える偽物。`fail_record` なら迂回の記録のコメントを断る。"""

    admin_merged: list[int] = field(default_factory=list)
    fail_record: bool = False

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> Any:
        if self.fail_record and method == "POST" and path.endswith("/comments") and records_trail.ADMIN_MARK in payload["body"]:
            raise GitHubError("書けない", 403)
        return super().rest(method, path, payload, params)

    def merge_as_admin(self, number: int, expect_head: str, method: str | None = None) -> MergeOutcome:
        self.admin_merged.append(number)
        self.merge_methods.append(method)
        return MergeOutcome(True, None, f"#{number} を迂回でマージした", self.pulls[number].head_sha)


def _admin_lander(hub: FakeHub, admin: bool = True, approvals: int = 1, failed: bool = False, wait: Any = None) -> land.Lander:
    base = release_lander(hub, failed, [])
    rules = hub.responses[("GET", "/repos/o/r/rules/branches/main")]
    hub.responses[("GET", "/repos/o/r/rules/branches/main")] = [*rules, *([REVIEW_RULE] if approvals else [])]
    hub.responses[("GET", "/repos/o/r/branches/main/protection/required_pull_request_reviews")] = not_found()
    hub.responses[("POST", "/repos/o/r/pulls")] = {"number": 8}
    return land.Lander(hub, base.production, base.budget, wait=wait if wait is not None else base.wait, admin=admin)


def _mvv_trail(sha: str = HEAD) -> Authored:
    return Authored("octocat", "OWNER", approval_decide.render_trail("approved", sha, trail.MVV_BY))


def _records(hub: FakeHub, number: int = 7) -> list[str]:
    return [c.body for c in hub.comments_of.get(number, []) if records_trail.ADMIN_MARK in c.body]


def test_admin_declaration_merges_the_production_pr_with_the_bypass_and_records_it() -> None:
    """受け入れ条件 8・I7・I8: 宣言があり承認数 1 なら、迂回でマージし、記録を置き、admin が真。"""
    hub = AdminHub(pulls={7: pull()}, comments_of={7: [trail_comment("approved", HEAD)]})
    done = _admin_lander(hub).land(7)
    assert done.merged and done.admin and done.admin_record == "https://example.invalid/7" and done.record_error is None
    assert hub.admin_merged == [7] and hub.merged == [] and hub.merge_methods == ["merge"]
    (record,) = _records(hub)
    assert HEAD in record and approval_decide.TRAIL_MARK not in record


def test_admin_declaration_stops_like_the_plain_merge_without_merging() -> None:
    """受け入れ条件 8（条件 2〜4）: 証跡が無い・チェックが落ちた・待つ間に差し戻されたら止まり、迂回しない。"""
    hub = AdminHub(pulls={7: pull()})
    done = _admin_lander(hub).land(7)
    assert not done.merged and done.kind == "refused" and hub.admin_merged == [] and not done.admin
    hub = AdminHub(pulls={7: pull()}, comments_of={7: [trail_comment("approved", HEAD)]})
    done = _admin_lander(hub, failed=True).land(7)
    assert not done.merged and done.kind == "test_failed" and hub.admin_merged == []
    hub = AdminHub(pulls={7: pull()}, comments_of={7: [trail_comment("approved", HEAD)]})

    def wait(reader: Callable[[], ci.Reading], budget: float) -> ci.WatchResult:
        hub.comments_of[7].append(trail_comment("rejected", HEAD))
        return ci.WatchResult(ci_reading(False), 1, 0.0, 0.0)

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
    hub = AdminHub(pulls={7: pull()}, comments_of={7: [trail_comment("approved", HEAD), rejection]})
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
    fake = records_trail.render_admin_record(HEAD, "")
    hub = AdminHub(
        pulls={7: pull()},
        comments_of={
            7: [trail_comment("approved", HEAD), Authored("mallory", "NONE", fake), Authored("octocat", "OWNER", fake, edited=True)]
        },
    )
    wrote, url = records_trail.write_admin_record(hub, 7, HEAD)
    assert wrote and url == "https://example.invalid/7" and len(_records(hub)) == 3
    assert records_trail.write_admin_record(hub, 7, HEAD)[0] is False


def test_authored_from_rest_marks_edited_comments() -> None:
    raw = {"user": {"login": "u"}, "author_association": "owner", "body": "b", "created_at": "t1", "updated_at": "t1"}
    assert not Authored.from_rest(raw).edited
    assert Authored.from_rest({**raw, "updated_at": "t2"}).edited


@pytest.mark.parametrize("admin, approvals", [(False, 1), (True, 0)])
def test_without_the_bypass_the_merge_stays_as_it_was(admin: bool, approvals: int) -> None:
    """受け入れ条件 8（迂回しない）・I2b: 宣言が無い・承認数 0 なら今のマージで、記録を置かず admin は偽。"""
    hub = AdminHub(pulls={7: pull()}, comments_of={7: [trail_comment("approved", HEAD)]})
    done = _admin_lander(hub, admin=admin, approvals=approvals).land(7)
    assert done.merged and not done.admin and done.admin_record is None
    assert hub.admin_merged == [] and hub.merged == [7] and _records(hub) == []


def test_approvals_reads_rules_then_protection_and_none_when_unreadable() -> None:
    hub = FakeHub()
    lander = land.Lander(hub, "main", 1.0, admin=True)
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
    hub = AdminHub(pulls={7: pull(merged=True)}, comments_of={7: [trail_comment("approved", HEAD)]})
    hub.responses[("GET", "/repos/o/r/pulls")] = [{"number": 9}]
    first = _admin_lander(hub).land(7)
    second = _admin_lander(hub).land(7)
    assert first.admin and first.admin_record and second.admin and len(_records(hub)) == 1 and hub.admin_merged == []
    hub = AdminHub(pulls={7: pull(merged=True)}, comments_of={7: [trail_comment("approved", OTHER)]})
    hub.responses[("GET", "/repos/o/r/pulls")] = [{"number": 9}]
    done = _admin_lander(hub).land(7)
    assert not done.admin and _records(hub) == []


def test_admin_record_failure_is_reported_apart_from_the_merge() -> None:
    """決定 7: 記録を書けなければ、マージは済んだまま record_error に理由を残す。"""
    hub = AdminHub(pulls={7: pull()}, comments_of={7: [trail_comment("approved", HEAD)]}, fail_record=True)
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


def test_issue_3487_production_merge_stops_before_merging_with_untrusted_token() -> None:
    """#3487: 書く token が信用されなければ、本番ブランチへ入れる前に止まる（マージの後の記録が必ず落ちる）。"""
    hub = FakeHub(pulls={7: pull()}, comments_of={7: [trail_comment("approved", HEAD)]})
    hub.trusted = set()
    done = release_lander(hub, False, []).land(7)
    assert not done.merged and done.kind == "refused" and "NDF_RELEASE_TOKEN" in done.detail and hub.merged == []


# --- #3572: 承認した先端だけを本番ブランチへ入れ、承認の後にコードを直さない -----------------------------

MERGE_SHA = "c" * 40


def _gate_trail(sha: str) -> Authored:
    """承認ゲートの答えの証跡（`trail.answer` が書く形）。"""
    body = f"{approval_decide.render_trail('approved', sha, trail.USER_BY)}{trail.ROUTE_LINE} {merge_decide.GATE_ROUTE}\n"
    return Authored("octocat", "OWNER", body)


def _merged_raw(head: str, by: str = "takemi", merged: bool = True) -> dict:
    return {
        "merged": merged,
        "merged_by": {"login": by} if merged else None,
        "merge_commit_sha": MERGE_SHA if merged else None,
        "head": {"sha": head, "ref": "release/v1.2.4"},
        "base": {"ref": "main"},
    }


def _approved(order: int, sha: str, route: str, by: str = "octocat") -> merge_decide.Approved:
    return merge_decide.Approved(order, "octocat", sha, route, by)


def test_facts_of_reads_who_merged_which_head() -> None:
    got = merge_decide.facts_of(_merged_raw(HEAD))
    assert got == merge_decide.MergeFacts(True, "takemi", HEAD, MERGE_SHA, "main", "release/v1.2.4")
    assert merge_decide.facts_of({**_merged_raw(HEAD), "merged_by": None}).by == ""
    assert not merge_decide.facts_of(_merged_raw(HEAD, merged=False)).merged


def test_decide_reads_the_route_and_does_not_carry_an_approval_to_another_head() -> None:
    """受け入れ条件 7・8・I5: 承認ゲートの承認の先端でマージすれば gate。別の先端（B）を人がマージすれば、B への承認として
    github-merge と記録し、承認ゲートの承認の先端（A）を gate_head に残す。"""
    on_head = merge_decide.facts_of(_merged_raw(HEAD))
    gated = merge_decide.decide(on_head, [_approved(0, HEAD, merge_decide.GATE_ROUTE)])
    assert gated == merge_decide.Approval("gate", "octocat", HEAD, HEAD, MERGE_SHA, HEAD)
    moved = merge_decide.decide(merge_decide.facts_of(_merged_raw(OTHER)), [_approved(0, HEAD, merge_decide.GATE_ROUTE)])
    assert moved == merge_decide.Approval("github-merge", "takemi", OTHER, OTHER, MERGE_SHA, HEAD)
    nothing = merge_decide.decide(on_head, [])
    assert nothing is not None and (nothing.route, nothing.gate_head) == ("github-merge", None)
    by_merge = merge_decide.decide(on_head, [_approved(1, HEAD, merge_decide.MERGE_ROUTE, by="takemi")])
    assert by_merge is not None and (by_merge.route, by_merge.by, by_merge.gate_head) == ("github-merge", "takemi", None)
    assert merge_decide.decide(merge_decide.facts_of(_merged_raw(HEAD, merged=False)), []) is None


def test_suggested_reruns_only_passing_stops_and_rejects_the_rest() -> None:
    """決定 3: 一時的な止まり方は打ち直し、直しの要る止まり方は差し戻し。"""
    assert merge_decide.suggested("ci_pending") == merge_decide.suggested("infra_wait") == merge_decide.RERUN
    for kind in ("test_failed", "check_failed", "refused", "no-such-kind"):
        assert merge_decide.suggested(kind) == merge_decide.REJECT


def test_failed_required_check_stops_with_the_check_and_without_pushing_or_merging() -> None:
    """受け入れ条件 4・I4: 必須のチェックが落ちたら、落ちたチェックを結果に載せて止まり、push もマージもしない。"""
    hub = FakeHub(pulls={7: pull()}, comments_of={7: [trail_comment("approved", HEAD)]})
    done = release_lander(hub, True, []).land(7)
    assert not done.merged and done.kind == "test_failed" and [c.name for c in done.failed] == ["pytest"]
    assert hub.merged == [] and not [r for r in hub.requests if r[0] in ("POST", "PATCH", "PUT") and "/git/" in r[1]]
    assert hub.pulls[7].head_sha == HEAD


def _values(out: dict) -> dict:
    return {i["name"]: i["value"] for i in out["items"] if i["kind"] == release_cli.VALUE_KIND}


def _last(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_cli_merge_reports_the_failed_check_and_two_next_moves(monkeypatch, capsys, tmp_path) -> None:
    """受け入れ条件 4: 止まった結果は items に落ちたチェック、値の項目 moves に 2 つの手、既定の手は差し戻し。"""
    hub = FakeHub(pulls={7: pull()}, comments_of={7: [trail_comment("approved", HEAD)]})
    lander = release_lander(hub, True, [])
    monkeypatch.setattr(release_cli, "_hub", lambda args: hub)
    monkeypatch.setattr(land, "Lander", lambda h, p, budget, admin=False: lander)
    monkeypatch.setattr(release_cli.bump, "planned_for", lambda *a: None)
    code = release_cli.main(["merge", "--pr", "7", "--root", str(tmp_path), "--production", "main", "--budget", "1"])
    out = _last(capsys)
    assert code != 0 and out["items"][0] == {"kind": "check", "name": "pytest", "outcome": "failure"}
    values = _values(out)
    assert set(values["moves"]) == set(merge_decide.NEXT_MOVES) == {"rerun", "reject"} and values["suggested"] == "reject"
    assert values["moves"]["reject"]["argv"][1:3] == ["release-steps", "answer"] and "next" not in out


def test_moved_head_after_the_approval_is_not_merged_and_names_both_heads() -> None:
    """受け入れ条件 6・I3: 承認の後に先端が A から B へ動けば、マージせずに止まり、2 つの先端を理由に出す。"""
    hub = FakeHub(pulls={7: pull(head=OTHER)}, comments_of={7: [_gate_trail(HEAD)]})
    done = release_lander(hub, False, []).land(7)
    assert not done.merged and done.kind == "refused" and hub.merged == []
    assert HEAD[:12] in done.detail and OTHER[:12] in done.detail
    assert trail.read(hub, 7).judgment.state == approval_decide.STALE


def test_gate_approval_merge_reports_the_gate_route() -> None:
    """受け入れ条件 5: 承認ゲートで承認した先端を、その先端との照合付きでマージし、結果に経路 gate を載せる。"""
    hub = FakeHub(pulls={7: pull()}, comments_of={7: [_gate_trail(HEAD)]})
    hub.responses[("POST", "/repos/o/r/pulls")] = {"number": 8}
    hub.responses[("GET", "/repos/o/r/pulls/7")] = _merged_raw(HEAD, by="octocat")
    seen: list[str | None] = []
    merge = hub.merge

    def recorded(number, expect_head=None, method=None, require_method=False):
        seen.append(expect_head)
        return merge(number, expect_head, method, require_method)

    hub.merge = recorded  # type: ignore[method-assign]
    done = release_lander(hub, False, []).land(7)
    assert done.merged and seen[0] == HEAD
    assert done.approval is not None and (done.approval.route, done.approval.approved_head) == ("gate", HEAD)
    assert done.merge_record is None and not [c for c in hub.comments(7) if records_trail.MERGE_MARK in c.body]


def _resumed(comments: list[Authored], head: str) -> FakeHub:
    hub = FakeHub(pulls={7: pull(head=head, merged=True)}, comments_of={7: comments})
    hub.responses[("GET", "/repos/o/r/pulls/7")] = _merged_raw(head)
    return hub


def test_human_merge_while_the_gate_waits_is_recorded_once() -> None:
    """受け入れ条件 7: 人が先端 A でマージした PR の再開は、人のマージを記録し（マージした人・A・マージコミット）、同期の PR を作らない。"""
    hub = _resumed([], HEAD)
    judgment, facts = trail.answer_merged(hub, 7, "main")
    assert judgment.approved and facts.by == "takemi"
    first = release_lander(hub, False, []).land(7)
    second = release_lander(hub, False, []).land(7)
    assert first.merged and first.approval is not None and hub.merged == []
    assert (first.approval.route, first.approval.by, first.approval.merged_head) == ("github-merge", "takemi", HEAD)
    records = [c.body for c in hub.comments(7) if records_trail.MERGE_MARK in c.body]
    assert len(records) == 1 and f"`{HEAD}`" in records[0] and f"`{MERGE_SHA}`" in records[0] and "takemi" in records[0]
    assert second.merged and second.merge_record is not None


def test_human_merge_of_a_moved_head_keeps_both_heads() -> None:
    """受け入れ条件 8: A の承認の後に B が積まれ、人が B をマージしたら、B への承認として記録し、A を承認ゲートの先端に残す。"""
    hub = _resumed([_gate_trail(HEAD)], OTHER)
    done = release_lander(hub, False, []).land(7)
    assert done.approval == merge_decide.Approval("github-merge", "takemi", OTHER, OTHER, MERGE_SHA, HEAD)
    (record,) = [c.body for c in hub.comments(7) if records_trail.MERGE_MARK in c.body]
    trail = records_trail  # 先端の行の印は記録の側へ移った。期待する文字列は分割の前と同じ式で書く
    assert f"{trail.GATE_HEAD_LINE} `{HEAD}`" in record and f"{trail.MERGED_HEAD_LINE} `{OTHER}`" in record


def test_merge_record_failure_keeps_the_merge_and_reports_it(monkeypatch, capsys) -> None:
    """I5: 人のマージの記録を書けなければ、本番は出たまま record_error に理由を置く。"""
    hub = _resumed([], HEAD)

    def broken(*args, **kwargs):
        raise trail.TrailError("書けない")

    monkeypatch.setattr(records_trail, "write_merge_record", broken)
    done = release_lander(hub, False, []).land(7)
    assert done.merged and done.record_error == "書けない" and done.approval is not None


def test_answer_merged_writes_one_trail_and_refuses_unmerged_or_other_bases() -> None:
    hub = _resumed([], HEAD)
    trail.answer_merged(hub, 7, "main")
    trail.answer_merged(hub, 7, "main")
    found = trail.approvals(hub, 7)
    assert [(a.route, a.by, a.sha) for a in found] == [("github-merge", "takemi", HEAD)]
    with pytest.raises(trail.TrailError):
        trail.answer_merged(hub, 7, "production")
    hub.responses[("GET", "/repos/o/r/pulls/7")] = _merged_raw(HEAD, merged=False)
    with pytest.raises(trail.NotMerged):
        trail.answer_merged(hub, 7, "main")


def test_approvals_reads_old_trails_as_the_gate_and_skips_rejections_and_untrusted() -> None:
    comments = [trail_comment("approved", HEAD), trail_comment("rejected", HEAD), trail_comment("approved", HEAD, author="mallory")]
    hub = FakeHub(pulls={7: pull()}, comments_of={7: comments})
    assert [(a.order, a.route, a.by) for a in trail.approvals(hub, 7)] == [(0, "gate", "octocat")]


def test_merge_record_is_written_once_per_head_by_a_trusted_author() -> None:
    approval = merge_decide.Approval("github-merge", "takemi", HEAD, HEAD, MERGE_SHA, None)
    assert "（無し）" in records_trail.render_merge_record(
        approval
    ) and approval_decide.TRAIL_MARK not in records_trail.render_merge_record(approval)
    with pytest.raises(ValueError):
        records_trail.render_merge_record(approval._replace(merge_sha="x"))
    forged = Authored("mallory", "NONE", records_trail.render_merge_record(approval))
    hub = FakeHub(pulls={7: pull()}, comments_of={7: [forged]})
    assert records_trail.write_merge_record(hub, 7, approval)[0]
    assert not records_trail.write_merge_record(hub, 7, approval)[0]


def test_cli_answer_routes(monkeypatch, capsys) -> None:
    """--route を省けば gate（答え・提示・担い手が要る）。github-merge はマージされていなければ not_applicable。"""
    hub = _resumed([], HEAD)
    monkeypatch.setattr(release_cli.bump, "planned_for", lambda *a: "1.2.4")
    hub.responses[("GET", "/repos/o/r/pulls/7")] = _merged_raw(HEAD, merged=False)
    monkeypatch.setattr(release_cli, "_hub", lambda args: hub)
    release_cli.main(["answer", "--pr", "7", "--answer", "approved"])
    assert _last(capsys)["cause"]["kind"] == "invalid"
    release_cli.main(["answer", "--pr", "7", "--route", "github-merge", "--production", "main"])
    assert _last(capsys)["cause"]["kind"] == "not_applicable"
    hub.responses[("GET", "/repos/o/r/pulls/7")] = _merged_raw(HEAD)
    assert release_cli.main(["answer", "--pr", "7", "--route", "github-merge", "--production", "main"]) == 0
    out = _last(capsys)
    assert _values(out) == {"state": "approved", "by": "takemi", "head": HEAD} and out["outputs"] == {"version": "1.2.4"}


def test_cli_merge_of_an_approved_pr_writes_only_the_version_into_the_outputs(monkeypatch, capsys, tmp_path) -> None:
    """結果の outputs はスプリントの文脈の語彙（版）だけで、承認と SHA は値の項目に載る（結果の契約に合う）。"""
    hub = FakeHub(pulls={7: pull()}, comments_of={7: [_gate_trail(HEAD)]})
    hub.responses[("POST", "/repos/o/r/pulls")] = {"number": 8}
    hub.responses[("GET", "/repos/o/r/pulls/7")] = _merged_raw(HEAD, by="octocat")
    lander = release_lander(hub, False, [])
    monkeypatch.setattr(release_cli, "_hub", lambda args: hub)
    monkeypatch.setattr(land, "Lander", lambda h, p, budget, admin=False: lander)
    monkeypatch.setattr(release_cli.bump, "planned_for", lambda *a: "1.2.4")
    assert release_cli.main(["merge", "--pr", "7", "--root", str(tmp_path), "--production", "main", "--budget", "1"]) == 0
    out = _last(capsys)
    assert out["outputs"] == {"version": "1.2.4"} and _values(out)["approval"]["route"] == "gate"


# --- 現状固定: `release-steps merge`（cli.cmd_merge）の結果の組み方 -----------------------------------------
# NOTE: 現状固定テスト。期待値は今の実装の出力をそのまま写した（仕様ではない。リファクタリングの退行の検出用）。
#       Lander を、決まった Landed を返す偽物に替え、cmd_merge の分岐（マージ済み・記録の失敗・止まった種類の有無・
#       次の手の有無）と、Lander へ渡す引数（宣言から読む admin・宣言から読む本番ブランチ）だけを見る。

_RERUN = ["ndf", "release-steps", "merge", "--pr", "7", "--budget", "30.0"]
_ADMIN_FALSE = {"kind": "value", "name": "admin", "value": False}


def _moves_items(target: list[str], suggested: str) -> list[dict]:
    """次の手の値の項目。どの手も元の呼び出しの対象 `target`（`--root`・`--repo`・`--production`）を引き継ぐ。"""
    reject = {"argv": ["ndf", "release-steps", "answer", "--pr", "7", "--answer", "rejected", *target], "ask": ["--presented", "--by"]}
    moves = {
        "rerun": {"argv": [*_RERUN, *target]},
        "reject": reject,
    }
    return [{"kind": "value", "name": "moves", "value": moves}, {"kind": "value", "name": "suggested", "value": suggested}]


def _cli_merge(monkeypatch, capsys, done: land.Landed, root: Path, *extra: str) -> tuple[int, dict, dict]:
    hub = FakeHub()
    seen: dict[str, Any] = {}

    class _Fixed:
        def __init__(self, h, p, budget, admin=False) -> None:
            seen.update(hub=h is hub, production=p, budget=budget, admin=admin)

        def land(self, pr: int) -> land.Landed:
            seen["pr"] = pr
            return done

    monkeypatch.setattr(release_cli, "_hub", lambda args: hub)
    monkeypatch.setattr(land, "Lander", _Fixed)
    monkeypatch.setattr(release_cli.bump, "planned_for", lambda *a: "1.2.4")
    argv = ["merge", "--pr", "7", "--budget", "30", "--root", str(root), *extra]
    code = release_cli.main(argv)
    return code, _last(capsys), seen


def _seen(admin: bool) -> dict:
    return {"hub": True, "production": "main", "budget": 30.0, "admin": admin, "pr": 7}


def test_characterize_cli_merge_merged_lists_every_value_and_reads_admin_from_the_declaration(monkeypatch, capsys, tmp_path) -> None:
    (tmp_path / declaration.PROJECT_FILE).parent.mkdir(parents=True)
    (tmp_path / declaration.PROJECT_FILE).write_text(json.dumps({"production_merge": {"admin": True}}), encoding="utf-8")
    approval = merge_decide.Approval("gate", "octocat", HEAD, HEAD, MERGE_SHA, HEAD)
    done = land.Landed(
        True,
        None,
        "マージした",
        production_sha=MERGE_SHA,
        admin=True,
        admin_record="https://example.invalid/7",
        approval=approval,
        merge_record="https://example.invalid/m",
    )
    code, out, seen = _cli_merge(monkeypatch, capsys, done, tmp_path, "--production", "main")
    assert code == 0 and seen == _seen(True)
    assert out == {
        "tool": "release-steps",
        "status": "ok",
        "summary": "マージした",
        "items": [
            {"kind": "value", "name": "production_sha", "value": MERGE_SHA},
            {"kind": "value", "name": "admin", "value": True},
            {"kind": "value", "name": "admin_record", "value": "https://example.invalid/7"},
            {"kind": "value", "name": "approval", "value": approval.as_json()},
            {"kind": "value", "name": "merge_record", "value": "https://example.invalid/m"},
        ],
        "metrics": {},
        "outputs": {"version": "1.2.4"},
    }


def test_characterize_cli_merge_record_error_stops_as_unknown_without_moves(monkeypatch, capsys, tmp_path) -> None:
    done = land.Landed(True, None, "マージした", record_error="書けない")
    code, out, seen = _cli_merge(monkeypatch, capsys, done, tmp_path, "--production", "main")
    detail = "マージした。記録を書けなかった（本番は出ている）: 書けない"
    assert code == 1 and seen == _seen(False)
    assert out == {
        "tool": "release-steps",
        "status": "stopped",
        "summary": detail,
        "items": [_ADMIN_FALSE],
        "metrics": {},
        "cause": {"kind": "unknown", "detail": detail},
        "outputs": {"version": "1.2.4"},
    }


def test_merge_result_takes_the_landed_directly_and_matches_the_cli(monkeypatch, capsys, tmp_path) -> None:
    """結果の組み立て（`_merge_result`）へ Landed を直接渡した結果は、main 経由の出力と同じ。"""
    done = land.Landed(True, None, "マージした", record_error="書けない")
    _code, out, _seen_args = _cli_merge(monkeypatch, capsys, done, tmp_path, "--production", "main")
    args = release_cli._parser().parse_args(["merge", "--pr", "7", "--budget", "30", "--root", str(tmp_path), "--production", "main"])
    assert release_cli._merge_result(args, done, "1.2.4") == out


def test_characterize_cli_merge_merged_with_a_kind_stops_with_that_kind_without_moves(monkeypatch, capsys, tmp_path) -> None:
    done = land.Landed(True, "ci_pending", "マージキューを待つ")
    code, out, seen = _cli_merge(monkeypatch, capsys, done, tmp_path, "--production", "main")
    assert code == 1 and seen == _seen(False)
    assert out == {
        "tool": "release-steps",
        "status": "stopped",
        "summary": "マージキューを待つ",
        "items": [_ADMIN_FALSE],
        "metrics": {},
        "cause": {"kind": "ci_pending", "detail": "マージキューを待つ"},
        "outputs": {"version": "1.2.4"},
    }


def test_characterize_cli_merge_unmerged_without_a_kind_is_unknown_and_reads_production_from_the_declaration(
    monkeypatch, capsys, tmp_path
) -> None:
    """--production を省けば本番ブランチは宣言から読み、打ち直しの手にも --production を付けない。"""
    (tmp_path / declaration.BRANCH_FILE).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / declaration.BRANCH_FILE).write_text(json.dumps({declaration.PRODUCTION_KEY: "main"}), encoding="utf-8")
    code, out, seen = _cli_merge(monkeypatch, capsys, land.Landed(False, None, "止まった"), tmp_path)
    assert code == 1 and seen == _seen(False)
    assert out == {
        "tool": "release-steps",
        "status": "stopped",
        "summary": "止まった",
        "items": [_ADMIN_FALSE, *_moves_items(["--root", str(tmp_path)], "reject")],
        "metrics": {},
        "cause": {"kind": "unknown", "detail": "止まった"},
        "outputs": {"version": "1.2.4"},
    }


def test_characterize_cli_merge_unmerged_lists_failed_checks_first_then_the_moves(monkeypatch, capsys, tmp_path) -> None:
    failed = (ci.Check("pytest", "done", "failure", 1),)
    done = land.Landed(False, "ci_pending", "待ちきれない", failed=failed)
    code, out, seen = _cli_merge(monkeypatch, capsys, done, tmp_path, "--production", "main")
    assert code == 1 and seen == _seen(False)
    assert out == {
        "tool": "release-steps",
        "status": "stopped",
        "summary": "待ちきれない",
        "items": [
            {"kind": "check", "name": "pytest", "outcome": "failure"},
            _ADMIN_FALSE,
            *_moves_items(["--root", str(tmp_path), "--production", "main"], "rerun"),
        ],
        "metrics": {},
        "cause": {"kind": "ci_pending", "detail": "待ちきれない"},
        "outputs": {"version": "1.2.4"},
    }


def test_cli_merge_after_the_production_merge_offers_only_resuming(monkeypatch, capsys, tmp_path) -> None:
    """本番へのマージが済んで後の処理だけが止まったら、差し戻す手を出さず、マージ済みからの再開（打ち直し）を既定にする。"""
    done = land.Landed(False, "test_failed", "後の処理が止まった", production_sha=MERGE_SHA)
    target = ["--root", str(tmp_path), "--repo", "o/r", "--production", "main"]
    code, out, _seen_args = _cli_merge(monkeypatch, capsys, done, tmp_path, "--repo", "o/r", "--production", "main")
    values = _values(out)
    assert code == 1 and out["cause"]["kind"] == "test_failed"
    assert values["moves"] == {"rerun": {"argv": [*_RERUN, *target]}} and values["suggested"] == "rerun"


# --- #3572 の決定 7: 版上げの PR の迂回のマージ（land_bump） ------------------------------------------------


def _bump_hub(**kw: Any) -> AdminHub:
    return AdminHub(pulls={20: pull(number=20, ref="release/v1.3.0")}, **kw)


def test_land_bump_merges_only_a_version_only_pr_with_the_bypass_and_the_head_match() -> None:
    """版上げの PR は承認の証跡を読まず、差分が版だけでチェックが通れば、先端との照合付きの迂回で 1 回マージする。"""
    hub = _bump_hub()
    seen: list[str] = []
    merge_as_admin = hub.merge_as_admin

    def recorded(number: int, expect_head: str, method: str | None = None) -> MergeOutcome:
        seen.append(expect_head)
        return merge_as_admin(number, expect_head, method)

    hub.merge_as_admin = recorded  # type: ignore[method-assign]
    done = _admin_lander(hub).land_bump(20, lambda p: [])
    assert done.merged and done.admin and hub.admin_merged == [20] and seen == [HEAD] and hub.merged == []


def test_land_bump_refuses_paths_outside_the_version_files_and_stops_without_the_bypass() -> None:
    hub = _bump_hub()
    done = _admin_lander(hub).land_bump(20, lambda p: ["src/x.py"])
    assert not done.merged and done.kind == "refused" and "src/x.py" in done.detail and hub.admin_merged == []
    done = _admin_lander(hub, admin=False).land_bump(20, lambda p: [])
    assert not done.merged and done.kind == "precondition" and "未決 3" in done.detail and hub.admin_merged == []


def test_land_bump_stops_on_a_failed_check_without_merging() -> None:
    hub = _bump_hub()
    done = _admin_lander(hub, failed=True).land_bump(20, lambda p: [])
    assert not done.merged and done.kind == "test_failed" and hub.admin_merged == [] and hub.merged == []
