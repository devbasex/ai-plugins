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
from ndf.progress.run import ISSUE_SECTION, SECTION_END

FAILED_ROW = {"number": 5, "result": "failed"}


NOT_RUN_ROW = {"number": 5, "result": "not_run"}
PASSED_ROW = {"number": 5, "result": "passed"}


@pytest.mark.parametrize(
    ("number", "rows", "require", "decision", "reason"),
    [
        (5, [FAILED_ROW], False, cd.KEEP, None),
        (6, [FAILED_ROW], False, cd.CLOSE, None),
        (5, [PASSED_ROW], False, cd.CLOSE, "リリース後テストに不合格と保留の行が無い"),
        (5, [], False, cd.CLOSE, None),
        (5, [], True, cd.KEEP, "リリース後テストの記録にこの課題の行が無い"),
        (6, [FAILED_ROW], True, cd.KEEP, "リリース後テストの記録にこの課題の行が無い"),
    ],
)
def test_verdict_reads_the_record_even_without_the_flag(
    number: int, rows: list[dict], require: bool, decision: str, reason: str | None
) -> None:
    got = cd.verdict(number, rows, require=require)
    assert got[0] == decision
    if reason is not None:
        assert got == (decision, reason)


def test_verdict_leaves_a_not_run_row_to_a_person_and_prefers_failed() -> None:
    assert cd.verdict(5, [NOT_RUN_ROW, PASSED_ROW], require=True) == (cd.ASK, "保留（not_run）の行が 1 件ある。閉じるかを人が決める")
    assert cd.verdict(5, [NOT_RUN_ROW, FAILED_ROW], require=False) == (cd.KEEP, "リリース後テストに不合格の行が 1 件ある")
    assert cd.waiting_rows(5, [NOT_RUN_ROW, FAILED_ROW, {"number": 6, "result": "not_run"}]) == [NOT_RUN_ROW]
    assert cd.waiting_rows(5, None) == []


def test_verdict_without_a_record_depends_on_require() -> None:
    assert cd.verdict(5, None, require=False)[0] == cd.CLOSE
    assert cd.verdict(5, None, require=True) == (cd.KEEP, "リリース後テストの記録が無い")


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
