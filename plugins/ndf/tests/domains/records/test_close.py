"""スプリントを閉じる条件（#2293）・クリーンアップの工程の記録（#1893）・gws の認証（#2067）。"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any

import pytest

from ndf.adapters.github import Authored, GitHubError
from ndf.domains.records import cleanup_stage, gws
from ndf.domains.records import close_decide as cd
from ndf.progress.flow import CloseRule
from ndf.progress.records import ISSUE_SECTION, SECTION_END

FAILED_ROW = {"number": 5, "result": "failed"}


NOT_RUN_ROW = {"number": 5, "result": "not_run"}
PASSED_ROW = {"number": 5, "result": "passed"}


@pytest.mark.parametrize(
    ("number", "rows", "enforced", "decision", "reason"),
    [
        (5, [FAILED_ROW], False, cd.KEEP, None),
        (6, [FAILED_ROW], False, cd.CLOSE, None),
        (5, [PASSED_ROW], False, cd.CLOSE, "リリース後テストに不合格と保留の行が無い"),
        (5, [], True, cd.CLOSE, None),
    ],
)
def test_verdict_reads_the_record_even_without_the_flag(
    number: int, rows: list[dict], enforced: bool, decision: str, reason: str | None
) -> None:
    got = cd.verdict(number, rows, enforced=enforced)
    assert got.decided == decision
    if reason is not None:
        assert got.reason == reason


def test_verdict_leaves_a_not_run_row_to_a_person_and_prefers_failed() -> None:
    got = cd.verdict(5, [NOT_RUN_ROW, PASSED_ROW], enforced=True)
    assert (got.decided, got.reason) == (cd.ASK, "保留（not_run）の行が 1 件ある。閉じるかを人が決める")
    got = cd.verdict(5, [NOT_RUN_ROW, FAILED_ROW], enforced=False)
    assert (got.decided, got.reason) == (cd.KEEP, "リリース後テストに不合格の行が 1 件ある")
    assert cd.waiting_rows(5, [NOT_RUN_ROW, FAILED_ROW, {"number": 6, "result": "not_run"}]) == [NOT_RUN_ROW]
    assert cd.waiting_rows(5, None) == []


def test_verdict_without_a_record_depends_on_enforced() -> None:
    assert cd.verdict(5, None, enforced=False).decided == cd.CLOSE
    got = cd.verdict(5, None, enforced=True)
    assert (got.decided, got.reason) == (cd.KEEP, "リリース後テストの記録が無い")


@pytest.mark.parametrize(
    ("rows", "verified"),
    [
        ([PASSED_ROW], True),
        ([PASSED_ROW, {"number": 6, "result": "failed"}], True),
        ([{"number": 6, "result": "passed"}], False),
        ([], False),
        (None, False),
        ([PASSED_ROW, NOT_RUN_ROW], False),
        ([PASSED_ROW, FAILED_ROW], False),
    ],
)
def test_verdict_is_verified_only_when_every_row_of_the_issue_passed(rows: list[dict] | None, verified: bool) -> None:
    # 受け入れ条件 6・9: テストで確認できたのは、その課題の行があり、すべて合格のときだけ
    assert cd.verdict(5, rows, enforced=False).verified is verified
    assert cd.verdict(5, rows, enforced=False).release is None


def test_verdict_keeps_a_failed_issue_whatever_is_enforced() -> None:
    # 受け入れ条件 8・I2: 条件を外しても（enforced が偽でも）、渡した記録の不合格では閉じない
    for enforced in (True, False):
        assert cd.verdict(5, [PASSED_ROW, FAILED_ROW], enforced=enforced).decided == cd.KEEP


OTHER_FAILED = {"number": 6, "result": "failed"}
OTHER_NOT_RUN = {"number": 6, "result": "not_run"}


@pytest.mark.parametrize(
    ("rows", "decided", "reason", "verified"),
    [
        (
            [FAILED_ROW, dict(FAILED_ROW), OTHER_FAILED, NOT_RUN_ROW],
            cd.KEEP,
            "リリース後テストに不合格の行が 2 件ある",
            False,
        ),
        (
            [NOT_RUN_ROW, OTHER_FAILED, dict(NOT_RUN_ROW), OTHER_NOT_RUN, PASSED_ROW],
            cd.ASK,
            "保留（not_run）の行が 2 件ある。閉じるかを人が決める",
            False,
        ),
        ([PASSED_ROW, dict(PASSED_ROW), OTHER_FAILED, OTHER_NOT_RUN], cd.CLOSE, "リリース後テストに不合格と保留の行が無い", True),
        ([OTHER_FAILED, OTHER_NOT_RUN], cd.CLOSE, "リリース後テストに不合格と保留の行が無い", False),
        ([{"number": 5, "result": "skipped"}], cd.CLOSE, "リリース後テストに不合格と保留の行が無い", False),
        ([{"number": 5}], cd.CLOSE, "リリース後テストに不合格と保留の行が無い", False),
        ([{"result": "failed"}, PASSED_ROW], cd.CLOSE, "リリース後テストに不合格と保留の行が無い", True),
        ([{"number": "5", "result": "failed"}, PASSED_ROW], cd.CLOSE, "リリース後テストに不合格と保留の行が無い", True),
    ],
)
def test_verdict_counts_only_the_rows_of_the_issue(rows: list[dict], decided: str, reason: str, verified: bool) -> None:
    # 件数・合格の判定はその課題（number が一致する行）だけで数え、他の課題や number の無い行は混ざらない
    got = cd.verdict(5, rows, enforced=True)
    assert got == cd.Verdict(decided, reason, verified, None)


@pytest.mark.parametrize(
    ("rows", "decided", "verified"),
    [
        ([PASSED_ROW, FAILED_ROW], cd.KEEP, False),
        ([PASSED_ROW, NOT_RUN_ROW], cd.ASK, False),
        ([PASSED_ROW, OTHER_FAILED], cd.CLOSE, True),
        ([], cd.CLOSE, False),
    ],
)
def test_verdict_reads_a_one_shot_iterator_like_a_list(rows: list[dict], decided: str, verified: bool) -> None:
    # 記録は 1 回しか回せないイテレータでも、リストと同じ判定になる
    got = cd.verdict(5, (r for r in rows), enforced=True)
    assert got == cd.verdict(5, rows, enforced=True)
    assert (got.decided, got.verified) == (decided, verified)


def test_blocking_and_waiting_rows_keep_the_order_and_identity_of_the_issue_rows() -> None:
    first, second = dict(FAILED_ROW), dict(FAILED_ROW)
    rows = [first, OTHER_FAILED, NOT_RUN_ROW, second, OTHER_NOT_RUN]
    got = cd.blocking_rows(5, rows)
    assert got == [first, second] and got[0] is first and got[1] is second
    assert cd.blocking_rows(5, iter(rows)) == [first, second]
    assert cd.waiting_rows(5, iter(rows)) == [NOT_RUN_ROW]
    assert cd.blocking_rows(5, None) == []


WEB, WORKER = "web", "worker"
VALUE = "main abc1234"


def _release(*environments: str, unit: str | None = None, release: int = 20, confirmation: dict | None = None) -> dict[str, Any]:
    """`records` の出力の、バージョンの無いリリース記録 1 件（対象は web と worker）。"""
    got = {"version": VALUE, "release": release, "included": [11, release], "environments": list(environments)}
    got.update(commit="a" * 40, targets=[WEB, WORKER], confirmation=confirmation or {e: "cd" for e in environments})
    observed = {e: {"url": "https://web.example/version", "value": "a" * 40} for e, k in got["confirmation"].items() if k == "observed"}
    got |= {"evidence": observed} if observed else {}
    return got | ({"unit": unit} if unit is not None else {})


def _check(environments: tuple[str, ...], *rows: tuple[int, str], retest: str | None = None) -> dict[str, Any]:
    issues = [{"number": n, "result": r, "evidence": "確かめた"} | ({"retest_reason": retest} if retest else {}) for n, r in rows]
    return {"version": VALUE, "environments": list(environments), "issues": issues}


def _view(releases: list[dict[str, Any]], *checks: dict[str, Any], targets: list[str] | None = None) -> dict[str, Any]:
    """`release-steps records --version` の出力の outputs（代表は最後のリリース記録）。"""
    return {
        "release": releases[-1] if releases else None,
        "releases": releases,
        "targets": targets or [WEB, WORKER],
        "verifications": list(checks),
    }


BOTH = _release(WEB, WORKER)


def test_records_verdict_closes_when_every_target_is_reflected_and_passed() -> None:
    # 受け入れ条件 9: 対象の届け先のすべてで反映の記録があり、その課題の行が合格なら閉じてよく、verified が真
    got = cd.records_verdict(
        7, _view([_release(WEB), _release(WORKER)], _check((WEB,), (7, "passed")), _check((WORKER,), (7, "passed"))), True
    )
    assert got.decided == cd.CLOSE and got.verified is True
    assert got.release == {"complete": True, "missing": [], "unconfirmed": []}


@pytest.mark.parametrize(
    "view",
    [
        _view([_release(WEB)], _check((WEB,), (7, "passed"))),
        _view([], _check((WEB, WORKER), (7, "passed"))),
        # 同じ値でも、別の本番の PR・別のリリースの単位の記録では、まだの届け先を埋めない
        _view([_release(WEB), _release(WORKER, release=21), _release(WORKER, unit="b")], _check((WEB, WORKER), (7, "passed"))),
    ],
)
def test_records_verdict_keeps_the_issue_until_every_target_is_reflected(view: dict[str, Any]) -> None:
    # 決定 7: 反映がそろうまで閉じない（条件 1）。まだの届け先を返す
    view["release"] = view["releases"][0] if view["releases"] else None
    got = cd.records_verdict(7, view, False)
    assert got.decided == cd.KEEP and got.verified is False
    assert got.release == {"complete": False, "missing": [WORKER] if view["releases"] else [WEB, WORKER], "unconfirmed": []}


def test_records_verdict_without_the_issues_row_on_a_target_depends_on_enforced() -> None:
    # 受け入れ条件 9: 別の課題の行しかない届け先があると verified は偽。--require なら閉じない
    view = _view([BOTH], _check((WEB,), (7, "passed")), _check((WORKER,), (8, "passed")))
    got = cd.records_verdict(7, view, False)
    assert got.decided == cd.CLOSE and got.verified is False
    assert cd.records_verdict(7, view, True).decided == cd.KEEP


def test_records_verdict_is_not_hidden_by_a_later_retest_of_another_issue() -> None:
    # 受け入れ条件 8・9: 同じ届け先で別の課題だけを再テストした記録が後にあっても、その課題の不合格で閉じない
    view = _view([BOTH], _check((WEB, WORKER), (7, "failed"), (8, "failed")), _check((WEB, WORKER), (8, "passed"), retest="接続を直した"))
    assert cd.records_verdict(7, view, False).decided == cd.KEEP
    assert cd.records_verdict(8, view, False).decided == cd.CLOSE


def test_records_verdict_leaves_a_not_run_pair_to_a_person_and_prefers_failed() -> None:
    assert cd.records_verdict(7, _view([BOTH], _check((WEB,), (7, "not_run")), _check((WORKER,), (7, "passed"))), True).decided == cd.ASK
    view = _view([BOTH], _check((WEB,), (7, "not_run")), _check((WORKER,), (7, "failed")))
    assert cd.records_verdict(7, view, True).decided == cd.KEEP


def test_records_verdict_lists_targets_reflected_without_the_cd_result() -> None:
    # 決定 7: 反映の記録が CD の結果でない届け先は unconfirmed に区分つきで載る
    made = _release(WEB, WORKER, confirmation={WEB: "observed", WORKER: "attested"})
    got = cd.records_verdict(7, _view([made], _check((WEB, WORKER), (7, "passed"))), True)
    assert got.release == {
        "complete": True,
        "missing": [],
        "unconfirmed": [{"name": WEB, "kind": "observed"}, {"name": WORKER, "kind": "attested"}],
    }


def test_records_verdict_on_a_versioned_record_judges_only_the_last_check() -> None:
    # 受け入れ条件 9: バージョン付きの記録では complete は null で、代表と同じ値・単位の最後のテストの記録だけで判定する
    versioned = {"version": "1.2.3", "release": 20, "included": [11]}
    first = {"version": "1.2.3", "issues": [{"number": 7, "result": "failed", "evidence": "e"}]}
    last = {"version": "1.2.3", "issues": [{"number": 7, "result": "passed", "evidence": "e"}]}
    other_unit = {"version": "1.2.3", "unit": "b", "issues": [{"number": 7, "result": "failed", "evidence": "e"}]}
    got = cd.records_verdict(
        7, {"release": versioned, "releases": [versioned], "targets": None, "verifications": [first, last, other_unit]}, True
    )
    assert got.decided == cd.CLOSE and got.verified is True and got.release is None
    got = cd.records_verdict(7, {"release": versioned, "releases": [versioned], "targets": None, "verifications": []}, True)
    assert got.decided == cd.KEEP and got.release is None


REQUIRED = CloseRule(True, None)
EXCLUDED = CloseRule(False, "監視で確かめる")


@pytest.mark.parametrize(
    ("rules", "require", "verification", "retrospective"),
    [
        (
            {"release_verification": REQUIRED, "retrospective_line": REQUIRED},
            True,
            {"excluded": False, "reason": None, "enforced": True},
            {"excluded": False, "reason": None, "enforced": True},
        ),
        (
            {"release_verification": REQUIRED, "retrospective_line": REQUIRED},
            False,
            {"excluded": False, "reason": None, "enforced": False},
            {"excluded": False, "reason": None, "enforced": True},
        ),
        (
            {"release_verification": EXCLUDED, "retrospective_line": CloseRule(False, "週次の会")},
            True,
            {"excluded": True, "reason": "監視で確かめる", "enforced": False},
            {"excluded": True, "reason": "週次の会", "enforced": False},
        ),
    ],
)
def test_conditions_of_separates_the_setting_from_the_call(rules: dict, require: bool, verification: dict, retrospective: dict) -> None:
    # 受け入れ条件 6・7: 設定で外したか（excluded）と、今回の呼び出しで記録を求めたか（enforced）を別に返す
    got = {name: c._asdict() for name, c in cd.conditions_of(rules, require).items()}
    assert got == {"release_verification": verification, "retrospective_line": retrospective}


def _body(mark: str) -> str:
    return f"本文\n\n{ISSUE_SECTION}\n\n- [x] 実装 — 2026-11-14 22:08\n- [{mark}] クリーンアップ\n- [ ] デプロイ\n{SECTION_END}\n"


def test_mark_stage_checks_only_the_cleanup_line_once() -> None:
    got = cd.mark_stage(_body(" "), cd.CLEANUP_STAGE, "2026-11-15 01:00")
    assert got is not None and "- [x] クリーンアップ — 2026-11-15 01:00" in got and "- [ ] デプロイ" in got
    assert cd.mark_stage(got, cd.CLEANUP_STAGE, "later") is None
    assert cd.mark_stage("進行のセクションが無い", cd.CLEANUP_STAGE, "x") is None


@dataclass
class Hub:
    repo: str = "o/r"
    bodies: dict[int, str] = field(default_factory=dict)
    closing: dict[int, list[int]] = field(default_factory=dict)
    patched: list[int] = field(default_factory=list)
    broken: set[int] = field(default_factory=set)

    def nodes(self, numbers: Any, node: str, fields: str) -> dict[int, dict]:
        return {n: {"closingIssuesReferences": {"nodes": [{"number": i} for i in self.closing[n]]}} for n in numbers}

    def issue_body(self, number: int) -> Authored:
        if number in self.broken:
            raise GitHubError("読めない")
        return Authored("octocat", "OWNER", self.bodies[number])

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> Any:
        number = int(path.rsplit("/", 1)[1])
        self.bodies[number] = payload["body"]
        self.patched.append(number)


def test_record_cleanup_stage_records_skips_and_fails_per_issue() -> None:
    hub = Hub(bodies={1: _body(" "), 2: _body("x"), 3: ""}, closing={10: [1, 2], 11: [2, 3]}, broken={3})
    got = cleanup_stage.record_cleanup_stage(hub, [11, 10])  # type: ignore[arg-type]
    assert [(i["issue"], i["result"]) for i in got] == [(1, "recorded"), (2, "skipped"), (3, "failed")]
    assert hub.patched == [1] and "- [x] クリーンアップ" in hub.bodies[1]


def test_item_name_uses_the_issue_and_falls_back_to_the_pr() -> None:
    assert cleanup_stage.item_name({"issue": 3, "result": cleanup_stage.FAILED}) == "#3"
    assert cleanup_stage.item_name({"pr": 10, "result": cleanup_stage.FAILED}) == "#10"


def _gws_status(monkeypatch, stdout: str, code: int = 0) -> None:
    done = subprocess.CompletedProcess(["gws"], code, stdout=stdout, stderr="")
    monkeypatch.setattr(gws.subprocess, "run", lambda *a, **k: done)


def test_auth_status_refuses_a_failed_or_incomplete_answer(monkeypatch) -> None:
    full = {"auth_method": "none", "credential_source": "token_cache", "client_config_exists": True}
    _gws_status(monkeypatch, json.dumps(full))
    assert gws.auth_status() == full
    for stdout, code in ((json.dumps(full), 1), (json.dumps({"auth_method": "oauth"}), 0), ("[]", 0)):
        _gws_status(monkeypatch, stdout, code)
        with pytest.raises(gws.GwsError):
            gws.auth_status()


# 現状固定（I-008）: auth_status の例外のメッセージ・stderr・例外の連鎖。期待値は今の実装の出力である。
@pytest.mark.parametrize(
    ("stdout", "code", "message", "cause"),
    [
        pytest.param("{}", 1, "gws auth status が終了コード 1 で終わった", None, id="exit-1"),
        pytest.param(
            "not json", 0, "gws auth status を読めない: Expecting value: line 1 column 1 (char 0)", json.JSONDecodeError, id="not-json"
        ),
        pytest.param(
            "[]", 0, "gws auth status の出力に credential_source / auth_method / client_config_exists のどれかが無い", None, id="array"
        ),
        pytest.param(
            json.dumps({"auth_method": "oauth"}),
            0,
            "gws auth status の出力に credential_source / auth_method / client_config_exists のどれかが無い",
            None,
            id="missing-key",
        ),
    ],
)
def test_auth_status_error_keeps_the_message_stderr_and_cause(monkeypatch, stdout, code, message, cause) -> None:
    done = subprocess.CompletedProcess(["gws"], code, stdout=stdout, stderr="e\n")
    monkeypatch.setattr(gws.subprocess, "run", lambda *a, **k: done)
    with pytest.raises(gws.GwsError) as e:
        gws.auth_status()
    assert (str(e.value), e.value.stderr) == (message, "e\n")
    assert (type(e.value.__cause__) if e.value.__cause__ else None) is cause


def test_auth_status_that_cannot_start_has_no_stderr(monkeypatch) -> None:
    def boom(*a, **k):
        raise FileNotFoundError(2, "No such file", "gws")

    monkeypatch.setattr(gws.subprocess, "run", boom)
    with pytest.raises(gws.GwsError) as e:
        gws.auth_status()
    assert (str(e.value), e.value.stderr) == ("gws auth status を打てない: [Errno 2] No such file: 'gws'", "")
    assert isinstance(e.value.__cause__, FileNotFoundError)
