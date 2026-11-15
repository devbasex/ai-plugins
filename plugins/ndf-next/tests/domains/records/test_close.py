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


def test_closable_reads_the_record_even_without_the_flag() -> None:
    assert cd.closable(5, [FAILED_ROW], require=False)[0] is False
    assert cd.closable(6, [FAILED_ROW], require=False)[0] is True
    assert cd.closable(5, [{"number": 5, "result": "not_run"}], require=False)[0] is True


def test_closable_without_a_record_depends_on_require() -> None:
    assert cd.closable(5, None, require=False)[0] is True
    assert cd.closable(5, None, require=True)[0] is False


def _body(mark: str) -> str:
    return f"本文\n\n{ISSUE_SECTION}\n\n- [x] 実装 — 2026-11-14 22:08\n- [{mark}] クリーンアップ\n- [ ] デプロイ\n{SECTION_END}\n"


def test_mark_stage_checks_only_the_cleanup_line_once() -> None:
    got = cd.mark_stage(_body(" "), cd.CLEANUP_STAGE, "2026-11-15 01:00")
    assert got is not None and "- [x] クリーンアップ — 2026-11-15 01:00" in got and "- [ ] デプロイ" in got
    assert cd.mark_stage(got, cd.CLEANUP_STAGE, "later") is None
    assert cd.mark_stage("進行のセクションが無い", cd.CLEANUP_STAGE, "x") is None


def test_gws_client_secret_only_is_not_authenticated() -> None:
    no_token = {"auth_method": "none", "credential_source": "client_secret.json"}
    assert gws.authenticated(no_token, {}) is False
    assert gws.authenticated(no_token, {"GOOGLE_WORKSPACE_CLI_TOKEN": "t"}) is True
    assert gws.authenticated({"credential_source": "token_cache"}, {}) is True
    assert gws.authenticated({"credential_source": "none"}, {}) is False


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
