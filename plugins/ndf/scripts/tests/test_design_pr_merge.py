"""承認した設計のマージ（lib/design_pr_merge.py。#2507）。gh と merged-steps.py は偽のコマンドで置き換える。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import delivery  # noqa: E402
import design_pr_merge as dm  # noqa: E402

STAMP = "a" * 40
GREEN = [{"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}]
RULES = [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "ci"}]}}]


class Gh:
    """偽の gh と merged-steps.py。呼ばれたコマンドを calls に残す。"""

    def __init__(self, **kw):
        self.view = {"state": "OPEN", "headRefOid": STAMP, "baseRefName": "develop", "headRefName": "design/x", "statusCheckRollup": GREEN}
        self.view.update(kw.pop("view", {}))
        self.rules = kw.pop("rules", (0, json.dumps(RULES), ""))
        self.contents = kw.pop("contents", (1, "", "HTTP 404: Not Found"))
        self.probe = kw.pop("probe", "passed")
        self.mwg = kw.pop("mwg", (0, "{}", ""))
        self.calls: list[list[str]] = []

    def __call__(self, cmd, cwd=None, stdin=None):
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(self.view), ""
        if cmd[:2] == ["gh", "api"] and "/rules/branches/" in cmd[2]:
            return self.rules
        if cmd[:2] == ["gh", "api"] and "/contents/" in cmd[2]:
            return self.contents
        if "probe" in cmd:
            return 0, json.dumps({"status": "ok", "metrics": {"class": self.probe}}), ""
        if "merge-when-green" in cmd:
            return self.mwg
        raise AssertionError(f"思わぬ呼び出し: {cmd}")

    def called(self, word: str) -> list[list[str]]:
        return [c for c in self.calls if word in c]


@pytest.fixture(autouse=True)
def not_production(monkeypatch):
    box = {"value": delivery.NOT_PRODUCTION}
    monkeypatch.setattr(dm.delivery, "load_delivery", lambda root: None)
    monkeypatch.setattr(dm.delivery, "judge_target", lambda decl, target: delivery.Verdict(target, box["value"], "宣言による"))
    return box


def merge(gh, stamp=STAMP):
    return dm.merge_approved("/r", 501, stamp, run=gh)


def test_green_design_pr_at_the_approved_head_is_merged_without_cleanup_and_with_the_wait_limit():
    gh = Gh()
    out = merge(gh)
    argv = gh.called("merge-when-green")[0]
    assert out["result"] == "merged" and out["class"] == "passed" and out["branch"] == "design/x"
    assert argv[argv.index("--expect-head") + 1] == STAMP and "--no-cleanup" in argv and "--gate-approved" not in argv
    assert argv[argv.index("--timeout") :][:8] == dm.WAIT_ARGS and "--infra-gap" not in argv
    assert gh.called("probe")[0][-1] == "--act"


def test_moved_head_and_missing_stamp_do_not_merge():
    gh = Gh(view={"headRefOid": "b" * 40})
    assert merge(gh)["result"] == "head_moved" and not gh.called("probe")
    gh = Gh()
    assert merge(gh, "0" * 12)["result"] == "no_stamp" and merge(gh, "")["result"] == "no_stamp"
    assert not gh.called("merge-when-green")


def test_merged_and_closed_design_prs():
    assert merge(Gh(view={"state": "MERGED"}))["result"] == "already_merged"
    out = merge(Gh(view={"state": "CLOSED"}))
    assert out["result"] == "failed" and "CLOSED" in out["reason"]


@pytest.mark.parametrize("value", [delivery.PRODUCTION, delivery.UNDETERMINED])
def test_production_target_is_not_merged_and_no_check_is_read(not_production, value):
    not_production["value"] = value
    gh = Gh()
    assert merge(gh)["result"] == "production"
    assert gh.calls == [gh.calls[0]]  # gh pr view の 1 回だけ（probe・merge-when-green・承認資料を書かない）


@pytest.mark.parametrize("cls", ["running", "queued", "failed", "stale", "stale_again", "settled"])
def test_checks_that_are_not_green_do_not_call_merge_when_green(cls):
    gh = Gh(probe=cls)
    out = merge(gh)
    assert (out["result"], out["class"]) == ("not_green", cls) and not gh.called("merge-when-green")


def test_infra_wait_reruns_with_no_gap_and_counts_the_reruns_it_made():
    rerun = {"kind": "check", "name": "ci", "result": "infra_rerun", "run": "1"}
    gh = Gh(probe="infra", mwg=(0, json.dumps({"status": "ok", "items": [rerun, {**rerun, "run": "2"}]}), ""))
    out = dm.merge_approved("/r", 501, STAMP, run=gh, infra_budget=2)
    argv = gh.called("merge-when-green")[0]
    assert out["result"] == "merged" and out["infra_rerun"] == 2
    assert argv[argv.index("--infra-gap") + 1] == "0" and argv[argv.index("--infra-reruns") + 1] == "2"


def test_infra_wait_without_budget_does_not_call_merge_when_green():
    gh = Gh(probe="infra")
    out = dm.merge_approved("/r", 501, STAMP, run=gh, infra_budget=0)
    assert (out["result"], out["class"], out["infra_rerun"]) == ("not_green", "infra", 0) and not gh.called("merge-when-green")


def test_unregistered_required_check_does_not_merge():
    gh = Gh(view={"statusCheckRollup": [{"name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS"}]})
    out = merge(gh)
    assert (out["result"], out["class"]) == ("not_green", dm.UNREGISTERED) and "ci" in out["reason"] and not gh.called("probe")


def test_empty_checks_merge_only_when_there_are_no_rules_and_no_workflows():
    gh = Gh(view={"statusCheckRollup": []}, rules=(0, "[]", ""))
    assert merge(gh)["result"] == "merged"
    for gh in (
        Gh(view={"statusCheckRollup": []}, rules=(0, "[]", ""), contents=(0, "[]", "")),  # ワークフローがある
        Gh(view={"statusCheckRollup": []}, rules=(0, json.dumps(RULES), "")),  # 必須の検査がある
        Gh(view={"statusCheckRollup": []}, rules=(1, "", "HTTP 403")),  # 規則を読めない
        Gh(view={"statusCheckRollup": []}, rules=(0, "[]", ""), contents=(1, "", "HTTP 500")),  # ワークフローの有無を読めない
    ):
        out = merge(gh)
        assert (out["result"], out["class"]) == ("not_green", dm.UNREGISTERED) and not gh.called("merge-when-green")


@pytest.mark.parametrize(
    "mwg, want",
    [
        ((4, json.dumps({"summary": "衝突"}), ""), "conflict"),
        ((10, json.dumps({"summary": "承認ゲート 2"}), ""), "production"),
        ((75, json.dumps({"summary": "基盤待ち"}), ""), "not_green"),
        ((1, json.dumps({"summary": "60 秒で終わらない", "items": [{"kind": "check", "result": "pending"}]}), ""), "not_green"),
        ((1, json.dumps({"summary": "権限が無い", "items": [{"kind": "pr", "result": "stopped"}]}), ""), "failed"),
        ((1, json.dumps({"items": [{"kind": "pr", "result": "head_moved"}]}), ""), "head_moved"),
        ((2, "", "読めない"), "failed"),
    ],
)
def test_merge_when_green_exit_codes_map_to_results(mwg, want):
    out = merge(Gh(mwg=mwg))
    assert out["result"] == want and out["reason"]


def test_unreadable_output_never_raises():
    def broken(cmd, cwd=None, stdin=None):
        raise OSError("gh が無い")

    out = dm.merge_approved("/r", 501, STAMP, run=broken)
    assert out["result"] == "failed" and "gh が無い" in out["reason"]
    assert dm.merge_approved("/r", 501, STAMP, run=lambda cmd, cwd=None, stdin=None: (0, "壊れた", ""))["result"] == "failed"
