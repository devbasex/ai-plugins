"""merged_lib/checks.py: 同じチェックを最新の項目だけで数えることと、Runner が付かずに取り消されたジョブの
基盤待ち（再実行・待ち直し・終了コード 75）。#1645 #1768。

入力は PR 1753（古い FAILURE と新しい SUCCESS）と PR #1765（Runner が付かずに取り消された Lint）の形を写した
statusCheckRollup とジョブの JSON。gh は gh_parts.gh を差し替え、呼ばれた引数を数える。
"""

from __future__ import annotations

import argparse
import json
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
URL = "https://github.com/devbasex/ai-plugins/actions/runs/{run}/job/{job}"


def cr(wf, name, conclusion, run, job, started=None, completed=None, status="COMPLETED"):
    return {
        "__typename": "CheckRun",
        "workflowName": wf,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "startedAt": started,
        "completedAt": completed,
        "detailsUrl": URL.format(run=run, job=job),
    }


def ok(wf, run, job, name="check"):
    return cr(wf, name, "SUCCESS", run, job, "2026-10-05T04:40:00Z", "2026-10-05T04:41:00Z")


# PR 1753 の形: PR body decisions / check に、古い FAILURE（push の時）と新しい SUCCESS（本文の編集の時）
OLD_FAILURE = cr("PR body decisions", "check", "FAILURE", "37264758029", "111600000001", "2026-10-05T04:44:10Z", "2026-10-05T04:44:40Z")
NEW_SUCCESS = cr("PR body decisions", "check", "SUCCESS", "37264973318", "111619734523", "2026-10-05T04:46:35Z", "2026-10-05T04:47:05Z")
PR1753 = [ok("Glossary", "1", "11"), OLD_FAILURE, NEW_SUCCESS, ok("Script structure", "2", "12")]

# PR #1765 の形: Lint / lint が Runner の付かないまま取り消された
LINT_RUN, LINT_JOB = "37364001907", "111944000001"
CANCELLED_LINT = cr("Lint", "lint", "CANCELLED", LINT_RUN, LINT_JOB, "2026-10-05T19:55:00Z", "2026-10-05T20:11:00Z")
NO_RUNNER = {"id": int(LINT_JOB), "conclusion": "cancelled", "steps": [], "runner_name": ""}
PR1765 = [ok("Glossary", "1", "11"), CANCELLED_LINT]


class FakeGh:
    """gh_parts.gh の差し替え。`routes` は引数の先頭からの一致 → (終了コード, 出力の JSON か文字列) の並び。"""

    def __init__(self):
        self.calls, self.routes = [], []

    def on(self, prefix, out=None, code=0, stderr=""):
        self.routes.append((list(prefix), code, out, stderr))

    def __call__(self, args, cwd=None, **_kw):
        self.calls.append(list(args))
        for prefix, code, out, stderr in self.routes:
            if args[: len(prefix)] == prefix:
                text = out if isinstance(out, str) or out is None else json.dumps(out)
                return types.SimpleNamespace(returncode=code, stdout=text or "", stderr=stderr)
        return types.SimpleNamespace(returncode=1, stdout="", stderr="no route")

    def called(self, *prefix):
        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]


@pytest.fixture
def checks(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS / "lib"))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    from merged_lib import checks

    return checks


@pytest.fixture
def gh(checks, monkeypatch):
    fake = FakeGh()
    monkeypatch.setattr(checks.gh_parts, "gh", fake)
    return fake


class Clock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, gap):
        self.t += 60.0


@pytest.fixture
def clock(checks, monkeypatch):
    c = Clock()
    monkeypatch.setattr(checks.time, "monotonic", c.monotonic)
    monkeypatch.setattr(checks.time, "sleep", c.sleep)
    return c


def watch_of(checks, monkeypatch, views, **kw):
    seq = list(views)

    def view(_root, _n):
        rollup = seq.pop(0) if len(seq) > 1 else seq[0]
        return {"state": "OPEN", "headRefOid": "a", "statusCheckRollup": rollup}

    monkeypatch.setattr(checks, "pr_state", view)
    opts = dict(pr=5, timeout=3600.0, interval=10.0, recheck=1.0, no_checks_after=60.0, stale_after=300.0, infra_reruns=3, infra_gap=0.0)
    opts.update(kw)
    return checks.GreenWatch(".", argparse.Namespace(**opts))


def stopped(capsys, fn):
    with pytest.raises(SystemExit) as e:
        fn()
    return e.value.code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


# --- 同じチェックの束ね方（AC1〜AC5・AC14） -------------------------------------------


def test_pr1753_old_failure_is_superseded_and_passes(checks, gh, clock, monkeypatch):
    """AC1: 新しい実行の SUCCESS を結論とし、古い FAILURE は数えない。置き換わった失敗が items に 1 件載る。"""
    watch = watch_of(checks, monkeypatch, [PR1753])
    watch.wait()
    superseded = [i for i in watch.items if i["result"] == "superseded"]
    assert superseded == [
        {"kind": "check", "name": "PR body decisions / check", "result": "superseded", "run": "37264758029", "job": "111600000001"}
    ]
    assert {"kind": "check", "name": "PR body decisions / check", "result": "passed"} in watch.items


def test_newer_failure_is_not_hidden_by_older_success(checks, gh, clock, monkeypatch, capsys):
    """AC2: 新しい項目が FAILURE で古い項目が SUCCESS なら失敗で止まる。"""
    newer_failure = {**OLD_FAILURE, "startedAt": "2026-10-05T05:00:00Z", "completedAt": "2026-10-05T05:01:00Z"}
    watch = watch_of(checks, monkeypatch, [[NEW_SUCCESS, newer_failure]])
    code, out = stopped(capsys, watch.poll)
    assert code == 1 and "CI が失敗" in out["summary"]


def test_long_failure_finishing_later_is_newer_than_short_success(checks):
    from merged_lib import reading

    """#632 の規則: 早く始まって後に終わった失敗は、後に始まって先に終わった成功より新しい。"""
    long_failure = cr("W", "t", "FAILURE", "1", "10", "2026-10-05T01:00:00Z", "2026-10-05T03:00:00Z")
    short_success = cr("W", "t", "SUCCESS", "2", "20", "2026-10-05T02:00:00Z", "2026-10-05T02:10:00Z")
    latest, superseded = reading.fold_rollup([long_failure, short_success])
    assert [i.state for i in latest] == ["failed"] and superseded == []


def test_same_name_in_other_workflow_is_another_check(checks, gh, clock, monkeypatch, capsys):
    """AC3・AC5: workflow が違う同じ名前は別のチェック。summary と items は <workflowName> / <name> と run を持つ。"""
    glossary_failure = cr("Glossary", "check", "FAILURE", "9", "99", "2026-10-05T04:40:00Z", "2026-10-05T04:41:00Z")
    watch = watch_of(checks, monkeypatch, [[glossary_failure, NEW_SUCCESS]])
    code, out = stopped(capsys, watch.poll)
    assert code == 1
    assert out["summary"] == "#5 の CI が失敗: Glossary / check（run 9）"
    assert {"kind": "check", "name": "Glossary / check", "result": "failed", "run": "9", "job": "99"} in out["items"]


def test_no_cancellation_adds_no_gh_calls(checks, gh, clock, monkeypatch):
    """AC14: 取り消しの無い PR の読み直しでは、ジョブの照会も実行の読みも打たない。"""
    watch = watch_of(checks, monkeypatch, [PR1753])
    assert watch.poll()[0] == "wait"
    assert gh.calls == []


# --- Runner が付かなかった取り消し（AC6〜AC10） ------------------------------------------


def infra_routes(gh, job=NO_RUNNER, run_status="completed"):
    gh.on(["api", f"repos/{{owner}}/{{repo}}/actions/jobs/{LINT_JOB}"], job)
    gh.on(["run", "view", LINT_RUN, "--json", "status"], {"status": run_status})
    gh.on(["run", "rerun", LINT_RUN, "--failed"], "")


def test_runner_less_cancellation_reruns_once_then_merges(checks, gh, clock, monkeypatch):
    """AC6: Runner の付かない取り消しは失敗で止まらず、実行が終わった後に gh run rerun <run> --failed を 1 回打って待つ。"""
    infra_routes(gh)
    rerun_ok = cr("Lint", "lint", "SUCCESS", LINT_RUN, "111944000002", "2026-10-05T22:10:00Z", "2026-10-05T22:12:00Z")
    watch = watch_of(checks, monkeypatch, [PR1765, [ok("Glossary", "1", "11"), CANCELLED_LINT, rerun_ok]])
    watch.wait()
    assert gh.called("run", "rerun") == [["run", "rerun", LINT_RUN, "--failed"]]
    assert {"kind": "check", "name": "Lint / lint", "result": "infra_rerun", "run": LINT_RUN, "rerun": 1} in watch.items
    assert not [i for i in watch.items if i["result"] == "failed"]


def test_rerun_waits_until_the_run_is_done_and_the_gap_has_passed(checks, gh, clock, monkeypatch):
    """I7: 実行が走っている間と、終わってから --infra-gap 秒の内は再実行しない。"""
    infra_routes(gh, run_status="in_progress")
    watch = watch_of(checks, monkeypatch, [PR1765], infra_gap=300.0)
    assert watch.poll()[0] == "wait"
    gh.routes = []
    infra_routes(gh)
    assert watch.poll()[0] == "wait"  # 終わったのを見た（gap の内）
    assert gh.called("run", "rerun") == []
    clock.t += 300.0
    watch.poll()
    assert len(gh.called("run", "rerun")) == 1


def test_cancelled_job_is_queried_once_while_it_stays(checks, gh, clock, monkeypatch):
    """同じ取り消しのジョブが残る間は、読み直しのたびにジョブを照会し直さない（照会は job ID ごとに 1 回）。"""
    infra_routes(gh, run_status="in_progress")
    watch = watch_of(checks, monkeypatch, [PR1765], infra_gap=300.0)
    for _ in range(3):
        assert watch.poll()[0] == "wait"
    assert len(gh.called("api", f"repos/{{owner}}/{{repo}}/actions/jobs/{LINT_JOB}")) == 1


def test_infra_wait_stops_with_75_after_the_rerun_limit(checks, gh, clock, monkeypatch, capsys):
    """AC7: 再実行を上限まで続けても取り消しが残ると、終了コード 75・基盤待ちの summary・infra_wait の items で止まる。"""
    infra_routes(gh)
    watch = watch_of(checks, monkeypatch, [PR1765], infra_reruns=2)
    code, out = stopped(capsys, watch.wait)
    assert code == 75 and out["status"] == "stopped"
    assert "基盤待ち" in out["summary"] and "CI が失敗" not in out["summary"] and "再実行 2 回で解けない" in out["summary"]
    assert {"kind": "check", "name": "Lint / lint", "result": "infra_wait", "run": LINT_RUN, "job": LINT_JOB, "reruns": 2} in out["items"]
    assert out["next"] == "GitHub Actions の復旧の後に打ち直す: merged-steps.py merge-when-green 5"
    assert len(gh.called("run", "rerun")) == 2


def test_infra_wait_stops_with_75_when_rerun_job_stays_pending_at_the_deadline(checks, gh, clock, monkeypatch, capsys):
    """AC7・I8: 待ちの上限の時点で取り消しが無くても、再実行した実行のジョブが Runner を待って pending なら 75。"""
    infra_routes(gh)
    queued = cr("Lint", "lint", "", LINT_RUN, "111944000002", status="QUEUED")
    gh.on(
        ["run", "view", LINT_RUN, "--json", "status,attempt,jobs"],
        {"status": "queued", "jobs": [{"databaseId": 111944000002, "status": "queued"}]},
    )
    gh.on(["run", "list"], [])
    watch = watch_of(checks, monkeypatch, [PR1765, [ok("Glossary", "1", "11"), CANCELLED_LINT, queued]], timeout=30.0)
    code, out = stopped(capsys, watch.wait)
    assert code == 75 and "30 秒で解けない" in out["summary"] and "CI が失敗" not in out["summary"]
    assert [i["name"] for i in out["items"] if i["result"] == "infra_wait"] == ["Lint / lint"]


def test_rerun_failure_stops_as_infra_wait(checks, gh, clock, monkeypatch, capsys):
    """E7: gh run rerun が失敗したら基盤待ちとして 75 で止まる。"""
    infra_routes(gh)
    gh.routes.insert(0, (["run", "rerun"], 1, "", "HTTP 403"))
    watch = watch_of(checks, monkeypatch, [PR1765])
    code, out = stopped(capsys, watch.poll)
    assert code == 75 and f"gh run rerun {LINT_RUN} --failed が失敗: HTTP 403" in out["summary"]


def test_cancellation_with_steps_is_a_failure(checks, gh, clock, monkeypatch, capsys):
    """AC8: ステップが 1 件以上ある取り消しは失敗として止まり、再実行しない。"""
    infra_routes(gh, job={**NO_RUNNER, "steps": [{"name": "Set up job"}], "runner_name": "GitHub Actions 1"})
    watch = watch_of(checks, monkeypatch, [PR1765])
    code, out = stopped(capsys, watch.poll)
    assert code == 1 and "Lint / lint" in out["summary"]
    assert gh.called("run", "rerun") == []


def test_unreadable_job_is_a_failure_with_the_reason(checks, gh, clock, monkeypatch, capsys):
    """AC9: 取り消しのジョブを照会できなければ失敗として止まり、照会できなかったことを items に書く。"""
    watch = watch_of(checks, monkeypatch, [PR1765])
    code, out = stopped(capsys, watch.poll)
    assert code == 1
    failed = [i for i in out["items"] if i["result"] == "failed"]
    assert failed == [
        {
            "kind": "check",
            "name": "Lint / lint",
            "result": "failed",
            "run": LINT_RUN,
            "job": LINT_JOB,
            "reason": "取り消しのジョブを照会できない",
        }
    ]


def test_content_failure_wins_over_infra_wait(checks, gh, clock, monkeypatch, capsys):
    """AC10: 基盤待ちと中身の失敗が同時にあれば、再実行せず中身の失敗で止まる。"""
    infra_routes(gh)
    failure = cr("Glossary", "check", "FAILURE", "9", "99", "2026-10-05T04:40:00Z", "2026-10-05T04:41:00Z")
    watch = watch_of(checks, monkeypatch, [[failure, CANCELLED_LINT]])
    code, out = stopped(capsys, watch.poll)
    assert code == 1 and out["metrics"]["infra"] == 1
    assert gh.called("run", "rerun") == [] and gh.called("run", "view") == []
