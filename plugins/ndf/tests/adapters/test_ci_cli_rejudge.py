"""`ndf ci merge` が `GitHub.merge` の直前の確かめへ渡す判定し直し（`MergeExpect.rejudge`）の今の振る舞い（#4409）。

判定の後にマージ先の設定（`.ndf/project.json` の blob の oid）が変わると、確かめの応答の本文で判定し直す。
承認が要らなくなればそのままマージし、要るなら承認の証跡を数え直す。証跡があればマージし、無ければ
`--production-gate` の形で終え、token の利用者を読めなければ `precondition` で止まる。
"""

from __future__ import annotations

from typing import Optional

import pytest

from ndf import ci_cli
from ndf.adapters import github
from ndf.contracts import causes
from ndf.core import merge_approval

import merge_hub

MANUAL = merge_hub.project_text(merge_hub.strategy(deliveries="manual"))
AUTO = merge_hub.project_text(merge_hub.strategy(deliveries="auto"))
UNSET = merge_hub.project_text(merge_hub.strategy(deliveries=None, cd=None))
APPROVED = ["--approved-head", merge_hub.HEAD, "--approved-base", "main"]


def _hub(monkeypatch: pytest.MonkeyPatch, before: Optional[str], after: Optional[str], **kwargs) -> merge_hub.MergeHub:
    """判定のときは `before`、マージの直前の確かめでは `after` を設定の本文として返す偽物（oid は c1 → c2 と変わる）。"""
    fake = merge_hub.MergeHub(files={"main": (before, "c1")}, files_at_merge={"main": (after, "c2")}, **kwargs)
    monkeypatch.setattr(ci_cli, "_hub", lambda args: fake)
    return fake


def _approve(hub: merge_hub.MergeHub) -> None:
    target = merge_approval.MergeTarget(merge_hub.REPO, 7, "main", merge_hub.HEAD)
    body = merge_approval.render_merge_row(merge_approval.MergeRow("approved", "user", target, True, "r0"))
    hub.comments_of.setdefault(7, []).append(github.Authored(hub.login, "OWNER", body, author_id=hub.account_id))


def _run(capsys, last_result, *args: str) -> tuple[int, dict]:
    code = ci_cli.main(["merge", "--pr", "7", *args])
    return code, last_result(capsys.readouterr().out)


@pytest.mark.parametrize(
    ("after", "verdict"),
    [(MANUAL, "manual"), (merge_hub.project_text(merge_hub.strategy(base="main", production="release")), "not_production")],
)
def test_rejudge_merges_when_the_changed_config_needs_no_approval(monkeypatch, capsys, last_result, after, verdict):
    """設定が変わっても、判定し直して承認が要らなければ証跡を読まずにマージし、結果には判定し直した値を残す。"""
    hub = _hub(monkeypatch, MANUAL, after)
    code, found = _run(capsys, last_result, "--production-gate", "defer")
    assert code == 0 and hub.merged == [7]
    assert found["metrics"]["merge"] == "merged" and found["metrics"]["verdict"] == verdict
    assert "approved_by" not in found["metrics"] and "comments" not in hub.calls


def test_rejudge_to_no_approval_drops_the_first_approver(monkeypatch, capsys, last_result):
    """初めの判定で証跡を読んでも、判定し直して承認が要らなくなれば、結果は判定し直した値で担い手を残さない。"""
    hub = _hub(monkeypatch, AUTO, MANUAL)
    _approve(hub)
    code, found = _run(capsys, last_result, "--production-gate", "defer")
    assert code == 0 and hub.merged == [7]
    assert found["metrics"]["verdict"] == "manual" and "approved_by" not in found["metrics"]


@pytest.mark.parametrize(("after", "verdict"), [(AUTO, "automatic"), (UNSET, "unset"), (None, "unknown"), ("not json", "unknown")])
def test_rejudge_merges_when_a_trail_covers_the_target(monkeypatch, capsys, last_result, after, verdict):
    """判定し直して承認が要っても、今の承認の対象への証跡があればマージし、結果には判定し直した値と証跡の担い手を残す。"""
    hub = _hub(monkeypatch, MANUAL, after)
    _approve(hub)
    code, found = _run(capsys, last_result, "--production-gate", "defer")
    assert code == 0 and hub.merged == [7]
    assert found["metrics"]["verdict"] == verdict and found["metrics"]["approved_by"] == "user"
    assert "comments" in hub.calls


@pytest.mark.parametrize(("after", "verdict"), [(AUTO, "automatic"), (UNSET, "unset"), (None, "unknown"), ("not json", "unknown")])
def test_rejudge_without_a_trail_defers_with_the_new_verdict(monkeypatch, capsys, last_result, after, verdict):
    """証跡が無ければマージせず、`--production-gate defer` の形で判定し直した値を返す。"""
    hub = _hub(monkeypatch, MANUAL, after)
    code, found = _run(capsys, last_result, "--production-gate", "defer")
    assert code == 0 and hub.merged == []
    assert found["metrics"]["merge"] == "deferred" and found["metrics"]["verdict"] == verdict
    assert found["items"] == [{"kind": "pr", "number": 7}]


def test_rejudge_without_a_trail_is_approval_stale_for_the_stale_gate(monkeypatch, capsys, last_result):
    hub = _hub(monkeypatch, MANUAL, AUTO)
    code, found = _run(capsys, last_result, "--production-gate", "stale", *APPROVED)
    assert code == causes.exit_code("approval_stale") and hub.merged == []
    assert found["cause"]["kind"] == "approval_stale"


def test_rejudge_without_a_trail_asks_for_the_ask_gate(monkeypatch, capsys, last_result, tmp_path):
    """`--production-gate` を渡さなければ、判定し直した値で承認を求める（承認の対象のファイルを書く）。"""
    hub = _hub(monkeypatch, MANUAL, AUTO)
    out_file = tmp_path / "presented-merge.json"
    code, found = _run(capsys, last_result, "--presented-out", str(out_file))
    assert code == 10 and found["status"] == "gate" and hub.merged == []
    assert out_file.exists()


def test_rejudge_stops_with_precondition_when_the_login_is_unreadable(monkeypatch, capsys, last_result):
    """判定し直して承認が要り、token の利用者を読めなければ approval_stale でなく precondition で止まる。
    初めの判定では承認が要らないので、外すのは `GitHub.merge` の側（ここでは dequeue しない）。"""
    hub = _hub(monkeypatch, MANUAL, AUTO, pulls_of={7: merge_hub.pull(queued=True)})
    _approve(hub)
    hub.login, hub.account_id = "", 0
    code, found = _run(capsys, last_result, "--production-gate", "stale", *APPROVED)
    assert code == causes.exit_code("precondition") and hub.merged == [] and hub.dequeued == []
    assert found["cause"]["kind"] == "precondition" and "アカウント ID" in found["cause"]["detail"]


def test_rejudge_is_not_called_when_the_config_oid_is_unchanged(monkeypatch, capsys, last_result):
    """oid が同じなら本文が違っても判定し直さない（偽物の確かめと同じく、oid だけを照らす）。"""
    fake = merge_hub.MergeHub(files={"main": (MANUAL, "c1")}, files_at_merge={"main": (AUTO, "c1")})
    monkeypatch.setattr(ci_cli, "_hub", lambda args: fake)
    code, _ = _run(capsys, last_result, "--production-gate", "defer")
    assert code == 0 and fake.merged == [7]


def test_rejudge_reads_the_text_passed_by_the_merge_check(monkeypatch):
    """`MergeExpect.rejudge` は確かめの応答の本文を受け、承認が要らなければ None、要って証跡が無ければ止まる結果を返す。"""
    hub = _hub(monkeypatch, MANUAL, MANUAL)
    seen: list[github.MergeExpect] = []
    real = hub.merge

    def merge(number, expect=None, **kw):
        seen.append(expect)
        return real(number, expect=expect, **kw)

    monkeypatch.setattr(hub, "merge", merge)
    assert ci_cli.main(["merge", "--pr", "7", "--production-gate", "defer"]) == 0
    rejudge = seen[0].rejudge
    assert rejudge(MANUAL) is None
    stop = rejudge(AUTO)
    assert stop is not None and not stop.merged
    assert (stop.kind, stop.detail, stop.head_sha) == (ci_cli._REJUDGED, "automatic", merge_hub.HEAD)
    _approve(hub)
    assert rejudge(AUTO) is None
    hub.login, hub.account_id = "", 0
    stop = rejudge(AUTO)
    assert stop is not None and (stop.merged, stop.kind, stop.head_sha) == (False, "precondition", merge_hub.HEAD)
