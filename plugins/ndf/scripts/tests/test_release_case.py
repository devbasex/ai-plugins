"""リリースの場合の判定（#2773 の受け入れ条件 17〜19）。

- CD の行が無ければネットワークを使わずに `no-cd`
- CD の行があり承認数 0 なら `cd-no-review`、1 以上なら `cd-review`、読めなければ `cd-no-review`
- ruleset に規則が無ければ従来のブランチ保護を読み、保護が無ければ 0
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import branch_strategy  # noqa: E402
import delivery  # noqa: E402
import gh_call  # noqa: E402
import release_case as rc  # noqa: E402
import strategy_fixture  # noqa: E402

REPO = SCRIPTS.parents[2]


def declare_admin(monkeypatch, admin: bool, section: dict | None = None) -> None:
    """場合の判定が読むブランチ戦略の宣言を、迂回の宣言（`merge.admin`）が `admin` のものに差し替える。"""
    sec = section or strategy_fixture.section("web-develop")
    sec["merge"] = {**sec["merge"], "admin": admin, **({"method": "direct"} if admin else {})}
    monkeypatch.setattr(rc.repo, "branch_strategy_of", lambda root: branch_strategy.Strategy(sec))


@pytest.fixture(autouse=True)
def _no_admin(monkeypatch):
    """既定は迂回の宣言の無い宣言（テストの作業ディレクトリの宣言を読まない）。"""
    declare_admin(monkeypatch, False)


def pr_rule(n):
    return {"type": "pull_request", "parameters": {"required_approving_review_count": n}}


def test_required_approvals():
    assert rc.required_approvals([{"type": "required_status_checks"}]) is None
    assert rc.required_approvals([pr_rule(0)]) == 0
    assert rc.required_approvals([pr_rule(1), pr_rule(2)]) == 2


@pytest.mark.parametrize(
    "has_cd, approvals, case",
    [(False, 1, rc.NO_CD), (True, 1, rc.CD_REVIEW), (True, 0, rc.CD_NO_REVIEW), (True, None, rc.CD_NO_REVIEW)],
)
def test_decide_case(has_cd, approvals, case):
    got = rc.decide_case(has_cd, approvals, "")
    assert got.case == case and got.reason


@pytest.mark.parametrize(
    "approvals, case",
    [(1, rc.CD_ADMIN), (2, rc.CD_ADMIN), (0, rc.CD_NO_REVIEW), (None, rc.CD_NO_REVIEW)],
)
def test_decide_case_with_the_admin_declaration(approvals, case):
    """#3506 の I2a: 迂回の宣言があっても、承認数が 1 以上と読めるときだけ cd-admin。"""
    got = rc.decide_case(True, approvals, "", admin=True)
    assert got.case == case and got.reason
    assert rc.decide_case(False, approvals, "", admin=True).case == rc.NO_CD


@pytest.mark.parametrize(
    "case, has_cd, trail, admin, human",
    [
        (rc.NO_CD, False, False, False, False),
        (rc.CD_NO_REVIEW, True, True, False, False),
        (rc.CD_REVIEW, True, False, False, True),
        (rc.CD_ADMIN, True, True, True, True),
    ],
)
def test_case_attributes(case, has_cd, trail, admin, human):
    c = rc.Case(case, "", "", "")
    assert (c.has_cd, c.trail_approval, c.admin_merge, c.human_approval) == (has_cd, trail, admin, human)


@pytest.mark.parametrize(
    "case, user_trail, mvv_trail",
    [
        (rc.NO_CD, False, False),
        (rc.CD_NO_REVIEW, False, True),
        (rc.CD_REVIEW, False, False),
        (rc.CD_ADMIN, True, False),
    ],
)
def test_case_trail_attributes(case, user_trail, mvv_trail):
    """証跡の組み合わせは trail_approval と human_approval の積・差と一致する。"""
    c = rc.Case(case, "", "", "")
    assert (c.user_trail_only, c.mvv_may_trail) == (user_trail, mvv_trail)
    assert c.user_trail_only == (c.trail_approval and c.human_approval)
    assert c.mvv_may_trail == (c.trail_approval and not c.human_approval)


def fake(table):
    calls = []

    def run(args, stdin=None, cwd=None):
        calls.append(" ".join(args))
        for key, (code, out) in table.items():
            if key in " ".join(args):
                return gh_call.GhResult(code, out if code == 0 else "", "" if code == 0 else out)
        return gh_call.GhResult(1, "", "HTTP 403")

    return run, calls


def test_read_approvals_falls_back_to_branch_protection(monkeypatch):
    run, _ = fake({"rules/branches/main": (0, json.dumps([pr_rule(1)]))})
    monkeypatch.setattr(gh_call, "RUNNER", run)
    assert rc.read_approvals(".", "main") == (1, "")
    run, _ = fake({"rules/branches/main": (0, "[]"), "protection/required_pull_request_reviews": (1, "gh: Not Found (HTTP 404)")})
    monkeypatch.setattr(gh_call, "RUNNER", run)
    assert rc.read_approvals(".", "main") == (0, "")
    run, _ = fake(
        {
            "rules/branches/main": (0, "[]"),
            "protection/required_pull_request_reviews": (0, json.dumps({"required_approving_review_count": 2})),
        }
    )
    monkeypatch.setattr(gh_call, "RUNNER", run)
    assert rc.read_approvals(".", "main") == (2, "")
    run, _ = fake({})
    monkeypatch.setattr(gh_call, "RUNNER", run)
    n, why = rc.read_approvals(".", "main")
    assert n is None and "読めない" in why


def _decl(rows):
    return delivery.DeliveryDecl(base="develop", production="main", rows=rows)


AUTO = {"kind": "auto", "branch": "main", "versioned": True, "publishes_release": True}


def test_case_of_without_cd_does_not_call_gh(monkeypatch):
    monkeypatch.setattr(delivery, "load_delivery", lambda root: _decl([]))
    run, calls = fake({})
    monkeypatch.setattr(gh_call, "RUNNER", run)
    assert rc.case_of(".").case == rc.NO_CD and calls == []


def test_case_of_ai_plugins_shape_is_cd_no_review(monkeypatch):
    monkeypatch.setattr(delivery, "load_delivery", lambda root: _decl([AUTO]))
    run, _ = fake({"rules/branches/main": (0, json.dumps([pr_rule(0)]))})
    monkeypatch.setattr(gh_call, "RUNNER", run)
    assert rc.case_of(".").case == rc.CD_NO_REVIEW


def test_case_of_raises_on_broken_decl(monkeypatch):
    monkeypatch.setattr(delivery, "load_delivery", lambda root: delivery.DeliveryDecl(problems=["壊れた"]))
    with pytest.raises(rc.CaseUnreadable):
        rc.case_of(".")


@pytest.mark.parametrize("admin, case", [(True, rc.CD_ADMIN), (False, rc.CD_REVIEW)])
def test_case_of_reads_the_admin_declaration(monkeypatch, admin, case):
    monkeypatch.setattr(delivery, "load_delivery", lambda root: _decl([AUTO]))
    declare_admin(monkeypatch, admin)
    run, _ = fake({"rules/branches/main": (0, json.dumps([pr_rule(1)]))})
    monkeypatch.setattr(gh_call, "RUNNER", run)
    assert rc.case_of(".").case == case


def test_case_of_stays_cd_no_review_when_approvals_are_unreadable(monkeypatch):
    monkeypatch.setattr(delivery, "load_delivery", lambda root: _decl([AUTO]))
    declare_admin(monkeypatch, True)
    run, _ = fake({})
    monkeypatch.setattr(gh_call, "RUNNER", run)
    assert rc.case_of(".").case == rc.CD_NO_REVIEW


def test_ai_plugins_declaration_is_cd_admin(monkeypatch):
    """受け入れ条件 9: ai-plugins の宣言を場合の判定に通すと、承認数 1 で cd-admin になる。旧い宣言が残っていれば（移行は
    手順 2）、移行のコマンドが書く値を読む。"""
    import branch_strategy_legacy as legacy

    sys.path.insert(0, str(SCRIPTS))
    from project_lib import branch_strategy_migrate as bmig

    plan, _ = bmig.plan_migration(REPO, legacy.load_moves())
    project = plan.files.get("project.json") or json.loads((REPO / ".ndf" / "project.json").read_text(encoding="utf-8"))
    sec = project[branch_strategy.SECTION]
    monkeypatch.setattr(rc.repo, "branch_strategy_of", lambda root: branch_strategy.Strategy(sec))
    monkeypatch.setattr(delivery, "load_delivery", lambda root: _decl([AUTO]))
    run, _ = fake({"rules/branches/main": (0, json.dumps([pr_rule(1)]))})
    monkeypatch.setattr(gh_call, "RUNNER", run)
    assert rc.case_of(".").case == rc.CD_ADMIN


# 現状固定（I-002）: decide_case が返す Case の全項目と、case_at・case_or_stop の経路。
_NO_CD = ("手元の承認資料", "NDF")
_TRAIL = ("リリースの PR の証跡のコメント", "利用者のアカウント")


@pytest.mark.parametrize(
    "has_cd, approvals, admin, expected",
    [
        (False, None, False, rc.Case(rc.NO_CD, *_NO_CD, "本番チャネルへのマージで届く CD の行が無い")),
        (False, 2, True, rc.Case(rc.NO_CD, *_NO_CD, "本番チャネルへのマージで届く CD の行が無い")),
        (
            True,
            2,
            True,
            rc.Case(
                rc.CD_ADMIN,
                "リリースの PR の証跡のコメント",
                "利用者のアカウント（管理者の迂回）",
                "本番チャネルの承認数が 2 で、迂回の宣言がある",
            ),
        ),
        (True, 1, False, rc.Case(rc.CD_REVIEW, "PR のレビューの承認", "利用者のアカウント", "本番チャネルの承認数が 1")),
        (True, 0, False, rc.Case(rc.CD_NO_REVIEW, *_TRAIL, "本番チャネルの承認数が 0")),
        (True, None, True, rc.Case(rc.CD_NO_REVIEW, *_TRAIL, "本番チャネルの承認数が 0")),
    ],
)
def test_decide_case_full_fields(has_cd, approvals, admin, expected):
    assert rc.decide_case(has_cd, approvals, "", admin=admin) == expected


@pytest.mark.parametrize(
    "has_cd, approvals, admin, case",
    [(False, None, False, rc.NO_CD), (True, 1, True, rc.CD_ADMIN), (True, 1, False, rc.CD_REVIEW), (True, None, False, rc.CD_NO_REVIEW)],
)
def test_decide_case_why_overrides_reason(has_cd, approvals, admin, case):
    got = rc.decide_case(has_cd, approvals, "渡した理由", admin=admin)
    assert (got.case, got.reason) == (case, "渡した理由")


def test_case_at_without_repository(tmp_path):
    expected = rc.Case(rc.NO_CD, *_NO_CD, "リポジトリが無い")
    assert rc.case_at(None) == expected
    assert rc.case_at("") == expected
    assert rc.case_at(tmp_path / "無い") == expected


def test_case_at_on_unreadable_declaration(monkeypatch, tmp_path):
    monkeypatch.setattr(delivery, "load_delivery", lambda root: delivery.DeliveryDecl(problems=["壊れた"]))
    assert rc.case_at(tmp_path) == rc.Case(rc.NO_CD, *_NO_CD, "宣言を読めない")
    with pytest.raises(rc.CaseUnreadable):
        rc.case_at(tmp_path, strict=True)


def test_case_at_delegates_to_case_of(monkeypatch, tmp_path):
    monkeypatch.setattr(delivery, "load_delivery", lambda root: _decl([AUTO]))
    run, _ = fake({"rules/branches/main": (0, json.dumps([pr_rule(0)]))})
    monkeypatch.setattr(gh_call, "RUNNER", run)
    assert rc.case_at(tmp_path) == rc.Case(rc.CD_NO_REVIEW, *_TRAIL, "本番チャネルの承認数が 0")


def test_case_or_stop_raises_precondition(monkeypatch):
    monkeypatch.setattr(delivery, "load_delivery", lambda root: delivery.DeliveryDecl(problems=["壊れた"]))
    with pytest.raises(rc.StepError) as e:
        rc.case_or_stop(".")
    assert e.value.code == rc.EXIT_PRECONDITION and "壊れた" in str(e.value)
