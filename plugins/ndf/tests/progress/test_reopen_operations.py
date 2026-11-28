"""終えた工程の開き直し（`ndf progress retry --later`。#4520 の受け入れ条件 10〜13・16・17・19・20）。

ステートマシンの DB は本物で、作業（プラン）と GitHub は偽物にする。
"""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import merge_hub
import pytest
from result_json import last_result

from ndf.adapters.github import Authored, GitHubError
from ndf.core import merge_approval
from ndf.progress import command, gate, invoke, model, reopening, run, store
from ndf.progress.gate import asking, finishing

ISSUE = 4520
DESIGN = "設計"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lanes = _load("lanes")


class Records:
    def __init__(self) -> None:
        self.bodies: dict[int, str] = {}
        self.broken = False

    def read(self, number: int) -> str:
        if self.broken:
            raise GitHubError("書けない", 502)
        return self.bodies.get(number, "本文\n")

    def write(self, number: int, body: str) -> None:
        self.bodies[number] = body


@pytest.fixture
def env(repo, monkeypatch):
    """作業の偽物の `Env`。手で打つ `retry` が使う GitHub と進捗記録の保存先も偽物にする。"""
    values = lanes.values(repo, "sprint/s1")
    calls: list[SimpleNamespace] = []
    script: dict[str, list] = {}
    recs, hub = Records(), merge_hub.MergeHub()

    def invoker(r, cwd, work_dir, answer):
        calls.append(SimpleNamespace(step=r.step, inputs=r.inputs, work_dir=work_dir, retries=r.retries))
        queued = script.get(r.step)
        return queued.pop(0)(r) if queued else lanes.ok_result(r, values)

    e = run.Env(
        facts=lambda words, ctx, cwd: {w: True for w in words},
        launch=lambda st_, tid, cwd: None,
        invoker=invoker,
        prepare=lambda r, work_dir, cwd: None,
        records=lambda cwd: recs,
        sleep=lambda s: None,
    )
    monkeypatch.setattr(finishing, "github_records", lambda cwd: recs)
    monkeypatch.setattr(finishing, "shared_hub", lambda cwd: lambda: hub)
    return SimpleNamespace(env=e, values=values, calls=calls, script=script, records=recs, hub=hub)


def _new(capsys) -> str:
    code = command.main(["new", "s1", "--mode", "standard", "--issues", str(ISSUE)])
    out = last_result(capsys.readouterr().out)
    assert code == 0, out
    return out["metrics"]["machine_id"]


def _retry(capsys, tid: str, stage: str, *extra: str, reason: str = "変更を求められた") -> dict:
    finishing.main(["retry", tid, stage, "--reason", reason, *extra])
    return last_result(capsys.readouterr().out)


def _labels(st, tid) -> dict[str, str]:
    v = run.read(st, tid)
    return {s.name: v.rows[s.name].state for s in v.snap.stages if s.name in v.rows}


def _at_release_gate(st, tid, repo, env) -> None:
    """設計を承認し、次の承認ゲート（デプロイのリリース承認ゲート）まで進める。"""
    assert lanes.turn(st, tid, repo, env.env)["status"] == "gate"
    lanes.answer(st, tid, DESIGN, "approved", "承認する")
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["stage"] == "デプロイ", out


def _stage_names(out: dict) -> list[str]:
    return [i["stage"] for i in out["items"]]


def _events(st, tid) -> list:
    return run.read(st, tid).events


def test_a_finished_stage_is_reopened_with_the_later_stages(repo, st, env, capsys):
    tid = _new(capsys)
    _at_release_gate(st, tid, repo, env)
    before = _events(st, tid)
    done = [s for s, state in _labels(st, tid).items() if state == "finished"]
    disabled = {s for s, state in _labels(st, tid).items() if state == "disabled"}

    out = _retry(capsys, tid, DESIGN, "--later", "reopen")

    assert out["status"] == "ok", out
    labels = _labels(st, tid)
    names = _stage_names(out)
    assert names[0] == DESIGN and "デプロイ" in names and "要求と受け入れ条件" not in names
    assert all(labels[n] == "pending" for n in names)
    assert labels["要求と受け入れ条件"] == "finished" and {s for s, state in labels.items() if state == "disabled"} == disabled
    # 進みの無い後ろの工程は開き直さない
    assert "振り返り" not in names
    assert set(done) - {"要求と受け入れ条件", "作業場所の用意"} <= set(names)
    # 前の記録はそのまま残り、時刻と理由を持つ（受け入れ条件 17）
    after = _events(st, tid)
    assert after[: len(before)] == before
    added = after[len(before) :]
    assert {e.draft.kind for e in added} == {"reopened"} and all(e.draft.reason == "変更を求められた" and e.at for e in added)
    assert [a["answer"] for a in gate.answers(run.read(st, tid))] == ["approved"]
    # 外への操作を済ませた工程の警告（受け入れ条件 15）
    assert any(DESIGN in w and "取り消されない" in w for w in out["warnings"]), out


def test_next_runs_the_reopened_stages_again_with_new_work_dirs_and_outputs(repo, st, env, capsys):
    tid = _new(capsys)
    _at_release_gate(st, tid, repo, env)
    first = {c.step: c.work_dir for c in env.calls}
    _retry(capsys, tid, DESIGN, "--later", "reopen")
    v = run.read(st, tid)
    context = v.context()
    assert "design_pr" not in context and "pr" not in context and context["issues"] == [ISSUE]
    env.values["design_pr"] = 70
    before = len(env.calls)

    out = lanes.turn(st, tid, repo, env.env)

    assert out["status"] == "gate" and out["metrics"]["stage"] == DESIGN, out
    again = env.calls[before:]
    assert again[0].step == "design" and again[0].work_dir != first["design"] and again[0].retries == 0
    lanes.answer(st, tid, DESIGN, "approved", "承認する")
    before = len(env.calls)
    out = lanes.turn(st, tid, repo, env.env)
    assert out["metrics"]["stage"] == "デプロイ", out
    assert run.read(st, tid).context()["design_pr"] == 70
    assert {c.step for c in env.calls[before:]} >= {"design-merge", "implement"}


def test_keep_reopens_only_the_stage_and_next_skips_the_kept_stages(repo, st, env, capsys):
    tid = _new(capsys)
    _at_release_gate(st, tid, repo, env)
    labels = _labels(st, tid)
    context = run.read(st, tid).context()

    out = _retry(capsys, tid, DESIGN, "--later", "keep")

    assert out["status"] == "ok" and _stage_names(out) == [DESIGN], out
    assert _labels(st, tid) == {**labels, DESIGN: "pending"}
    assert run.read(st, tid).context()["pr"] == context["pr"]
    lanes.turn(st, tid, repo, env.env)
    lanes.answer(st, tid, DESIGN, "approved", "承認する")
    before = len(env.calls)
    out = lanes.turn(st, tid, repo, env.env)
    # 設計の残りを終えると、残した終了の工程を飛ばしてデプロイの回答待ちへ戻る
    assert out["status"] == "gate" and out["metrics"]["stage"] == "デプロイ", out
    assert [c.step for c in env.calls[before:]] == ["design-merge"]


@pytest.mark.parametrize(
    ("extra", "reason", "needle"),
    [
        ((), "変更", "--later"),
        (("--later", "reopen"), " ", "理由"),
    ],
)
def test_a_finished_stage_needs_later_and_a_reason(repo, st, env, capsys, extra, reason, needle):
    tid = _new(capsys)
    _at_release_gate(st, tid, repo, env)
    before = _events(st, tid)
    out = _retry(capsys, tid, DESIGN, *extra, reason=reason)
    assert out["cause"]["kind"] == "refused" and needle in out["cause"]["detail"], out
    assert _events(st, tid) == before and env.hub.calls == []


def test_a_failed_stage_keeps_the_same_retry(repo, st, env, capsys):
    tid = _new(capsys)
    env.script["worktree"] = [lambda r: invoke.StageResult(run.result.stopped("plan", "test_failed", "落ちた"), {})]
    lanes.turn(st, tid, repo, env.env)
    stage = "作業場所の用意"
    assert _labels(st, tid)[stage] == "failed"
    refused = _retry(capsys, tid, stage, "--later", "reopen")
    assert refused["cause"]["kind"] == "refused" and "--later" in refused["cause"]["detail"]
    before = _events(st, tid)

    out = _retry(capsys, tid, stage, reason="直した")

    assert out["status"] == "ok", out
    assert [(e.draft.kind, e.draft.row_key, e.draft.reason) for e in _events(st, tid)[len(before) :]] == [("retry", stage, "直した")]
    assert env.hub.calls == []


@pytest.mark.parametrize("stage", ["振り返り", "デプロイ"])
def test_a_pending_or_running_stage_is_refused_with_another_command(repo, st, env, capsys, stage):
    tid = _new(capsys)
    _at_release_gate(st, tid, repo, env)
    before = _events(st, tid)
    out = _retry(capsys, tid, stage, "--later", "reopen")
    assert out["cause"]["kind"] == "refused" and "ndf " in out["cause"]["detail"], out
    assert _events(st, tid) == before


def test_reopening_is_refused_while_work_is_running(repo, st, env, capsys):
    tid = _new(capsys)
    _at_release_gate(st, tid, repo, env)
    before = _events(st, tid)
    with store.run_lock(st, tid):
        out = _retry(capsys, tid, DESIGN, "--later", "reopen")
    assert out["cause"]["kind"] == "refused" and "ndf progress wait" in out["cause"]["detail"], out
    assert _events(st, tid) == before and env.hub.calls == []


def test_reopening_is_refused_while_launched_work_has_not_taken_the_lock(repo, st, env, capsys, monkeypatch):
    tid = _new(capsys)
    _at_release_gate(st, tid, repo, env)
    monkeypatch.setattr(run, "launching", lambda st_, mid: True)
    before = _events(st, tid)
    out = _retry(capsys, tid, DESIGN, "--later", "reopen")
    assert out["cause"]["kind"] == "refused", out
    assert _events(st, tid) == before


@pytest.mark.parametrize(("closed", "word"), [("cancelled", "中止"), ("completed", "完了")])
def test_a_closed_sprint_is_not_reopened(repo, st, env, capsys, closed, word):
    tid = _new(capsys)
    _at_release_gate(st, tid, repo, env)
    if closed == "cancelled":
        command.main(["cancel", tid, "--reason", "止める"])
        capsys.readouterr()
    else:
        run._write(st, tid, model.EventDraft("completed", "workflow", rev=1))
    before = _events(st, tid)
    out = _retry(capsys, tid, DESIGN, "--later", "reopen")
    assert out["cause"]["kind"] == "refused" and word in out["cause"]["detail"], out
    assert _events(st, tid) == before and env.hub.calls == []


def test_the_progress_tables_are_rewritten_and_a_failure_is_only_a_warning(repo, st, env, capsys):
    tid = _new(capsys)
    _at_release_gate(st, tid, repo, env)
    assert "- [x] 設計" in env.records.bodies[ISSUE]
    _retry(capsys, tid, DESIGN, "--later", "reopen")
    assert "- [x] 設計" not in env.records.bodies[ISSUE] and "- [ ] 設計" in env.records.bodies[ISSUE]
    # 文脈から外れた PR の欄も、前に記録した PR の欄を書き直す
    assert "設計 —" not in env.records.bodies[env.values["pr"]]

    env.records.broken = True
    lanes.turn(st, tid, repo, env.env)
    lanes.answer(st, tid, DESIGN, "approved", "承認する")
    lanes.turn(st, tid, repo, env.env)
    out = _retry(capsys, tid, DESIGN, "--later", "reopen")
    assert out["status"] == "ok" and any(f"ndf progress resync {tid}" in w for w in out["warnings"]), out
    assert _labels(st, tid)[DESIGN] == "pending"


def _approve_production(st, tid, env, target: merge_approval.MergeTarget) -> None:
    """設計承認ゲートに、本番へ反映される対象 `target` を見た利用者の承認を書き、証跡を PR へ置く。"""
    v = run.read(st, tid)
    seen = gate.Target(target.head, None, target.repo, target.number, target.base, True)
    q = replace(gate.question(v, run.decide(v)), target=seen)
    asking.write_material(st.root, v, q)
    shown = asking.read_presented(st.root, q).presentation
    answer = gate.Answer(q.id, "approved", "承認する", "human", f"prod-{q.id}", seen=gate.Target(head=target.head, presentation=shown))
    post = gate.github_post(Path.cwd(), hub=lambda: env.hub)  # type: ignore[arg-type,return-value]
    gate.answer(st, tid, answer, lambda q, *, material=True: seen, post)


def _judgment(env, target: merge_approval.MergeTarget):
    return merge_approval.merge_judgment(merge_approval.merge_rows(env.hub, target.number, env.hub.viewer()), target)


@pytest.mark.parametrize("later", ["reopen", "keep"])
def test_reopening_revokes_the_merge_approval_of_the_stage_once(repo, st, env, capsys, later):
    tid = _new(capsys)
    lanes.turn(st, tid, repo, env.env)
    target = merge_approval.MergeTarget(merge_hub.REPO, 7, "main", merge_hub.HEAD)
    _approve_production(st, tid, env, target)
    lanes.turn(st, tid, repo, env.env)
    assert _judgment(env, target) is not None

    out = _retry(capsys, tid, DESIGN, "--later", later)

    assert out["status"] == "ok", out
    assert _judgment(env, target) is None
    rows = merge_approval.merge_rows(env.hub, target.number, env.hub.viewer())
    assert [(r.answer, r.by) for r in rows] == [("approved", "user"), ("rejected", "reopen")]
    # 取り消した承認の PR は、キューに入っていれば GitHub がマージしないように外す
    assert env.hub.dequeued == [target.number]


def test_a_later_approval_from_elsewhere_is_not_revoked(repo, st, env, capsys):
    """同じ対象の最新の承認が開き直す工程の記録でなければ、取り消さない。"""
    tid = _new(capsys)
    lanes.turn(st, tid, repo, env.env)
    target = merge_approval.MergeTarget(merge_hub.REPO, 7, "main", merge_hub.HEAD)
    _approve_production(st, tid, env, target)
    lanes.turn(st, tid, repo, env.env)
    row = merge_approval.MergeRow("approved", "user", target, True, "another-machine")
    merge_approval.write_merge_row(env.hub, row, env.hub.viewer())

    out = _retry(capsys, tid, DESIGN, "--later", "keep")

    assert out["status"] == "ok", out
    assert _judgment(env, target) == row
    # 残る承認の PR はキューから外さない
    assert env.hub.dequeued == []


@pytest.mark.parametrize(("note", "revoked"), [(1, False), (2, True)])
def test_an_mvv_approval_is_matched_by_its_comment(note, revoked):
    """2 つのスプリントの MVV 判定は記録の識別子が同じになるので、判定のコメントの URL で取り消す承認を分ける。"""
    hub = merge_hub.MergeHub()
    target = merge_approval.MergeTarget(merge_hub.REPO, 7, "main", merge_hub.HEAD)
    row = merge_approval.MergeRow("approved", "mvv", target, True, f"mvv:design:{merge_hub.HEAD}")
    body = f"{merge_approval.MVV_NOTE_HEADING}design）\n{merge_approval.render_merge_row(row)}"
    hub.comments_of[7] = [Authored(hub.login, "OWNER", body, f"url-{n}", author_id=hub.account_id) for n in (1, 2)]

    revocation = merge_approval.Revocation(target, frozenset({f"url-{note}"}), "reopen:x")
    merge_approval.write_revocations(hub, [revocation], hub.viewer())

    assert ("post_comment" in hub.calls) is revoked


def test_revocations_read_the_comments_of_a_pr_once():
    hub = merge_hub.MergeHub()
    targets = [merge_approval.MergeTarget(merge_hub.REPO, 7, "main", head) for head in (merge_hub.HEAD, merge_hub.OTHER)]
    for n, target in enumerate(targets):
        merge_approval.write_merge_row(hub, merge_approval.MergeRow("approved", "user", target, True, str(n)), hub.viewer())
    hub.calls.clear()

    revocations = [merge_approval.Revocation(t, frozenset({str(n)}), f"reopen:{n}") for n, t in enumerate(targets)]
    merge_approval.write_revocations(hub, revocations, hub.viewer())

    assert hub.calls == ["comments", "post_comment", "post_comment"]
    assert hub.dequeued == [7]


def test_an_unfinished_stage_with_a_recorded_merge_is_warned(repo, st, env, capsys):
    """外への操作のステップが成功した工程は、終了していなくても取り消されないと警告する。"""
    tid = _new(capsys)
    lanes.turn(st, tid, repo, env.env)
    v = run.read(st, tid)
    merge = next(step.id for step in v.snap.stage(DESIGN).steps if step.kind == "merge")
    payload = {"step": merge, "status": "finished", "report": {}}
    draft = model.EventDraft("step_recorded", "workflow", DESIGN, payload=payload, rev=v.rev)
    merged = replace(v, events=[*v.events, model.Event(v.events[-1].seq + 1, v.events[-1].at, draft)])

    assert reopening.plan_reopen(v, DESIGN, later="keep", reason="r", trigger={"by": "retry"}).warnings == []
    warned = reopening.plan_reopen(merged, DESIGN, later="keep", reason="r", trigger={"by": "retry"}).warnings
    assert warned == [f"工程 {DESIGN} のマージ・リリースは取り消されない"]


def test_a_revocation_that_cannot_be_written_records_nothing(repo, st, env, capsys):
    tid = _new(capsys)
    lanes.turn(st, tid, repo, env.env)
    target = merge_approval.MergeTarget(merge_hub.REPO, 7, "main", merge_hub.HEAD)
    _approve_production(st, tid, env, target)
    lanes.turn(st, tid, repo, env.env)
    env.hub.account_id = 0  # token の利用者を読めない
    before = _events(st, tid)
    out = _retry(capsys, tid, DESIGN, "--later", "reopen")
    assert out["cause"]["kind"] == "refused", out
    assert _events(st, tid) == before


def _approved_design(capsys, repo, st, env) -> tuple[str, merge_approval.MergeTarget]:
    """設計を本番へ反映される対象で承認し、終了まで進めたステートマシン。"""
    tid = _new(capsys)
    lanes.turn(st, tid, repo, env.env)
    target = merge_approval.MergeTarget(merge_hub.REPO, 7, "main", merge_hub.HEAD)
    _approve_production(st, tid, env, target)
    lanes.turn(st, tid, repo, env.env)
    return tid, target


def test_an_approval_written_by_another_account_refuses_the_reopen(repo, st, env, capsys):
    tid, target = _approved_design(capsys, repo, st, env)
    before, comments = _events(st, tid), list(env.hub.comments_of[target.number])
    env.hub.login, env.hub.account_id = "someone", 2

    out = _retry(capsys, tid, DESIGN, "--later", "keep")

    detail = out["cause"]["detail"]
    assert out["cause"]["kind"] == "refused" and "承認を書いたアカウント" in detail and "octocat" in detail, out
    assert _events(st, tid) == before and env.hub.comments_of[target.number] == comments and env.hub.dequeued == []

    env.hub.login, env.hub.account_id = "octocat", 1
    assert _retry(capsys, tid, DESIGN, "--later", "keep")["status"] == "ok"


@pytest.mark.parametrize("gone", ["deleted", "edited", "revoked"])
def test_an_approval_that_no_account_can_merge_with_does_not_refuse(repo, st, env, capsys, gone):
    """証跡がどこにも無い・編集されて数えない・書いたアカウントで取り消し済みなら、別のアカウントでも開き直せる。"""
    tid, target = _approved_design(capsys, repo, st, env)
    comments = env.hub.comments_of[target.number]
    if gone == "deleted":
        comments.clear()
    elif gone == "edited":
        comments[:] = [c._replace(edited=True) for c in comments]
    else:
        trail = merge_approval.merge_rows(env.hub, target.number, env.hub.viewer())[0]
        revocation = merge_approval.Revocation(target, frozenset({trail.record_id}), "reopen:x")
        merge_approval.write_revocations(env.hub, [revocation], env.hub.viewer())
    env.hub.account_id = 2

    out = _retry(capsys, tid, DESIGN, "--later", "keep")

    assert out["status"] == "ok", out
    assert env.hub.dequeued[-1:] == [target.number]


def test_approvals_written_by_two_accounts_are_revoked_one_account_at_a_time():
    """自分の分を書いてから断るので、アカウントを替えて打ち直せば終わる。"""
    hub = merge_hub.MergeHub()
    target = merge_approval.MergeTarget(merge_hub.REPO, 7, "main", merge_hub.HEAD)
    for account in (1, 2):
        hub.account_id = account
        merge_approval.write_merge_row(hub, merge_approval.MergeRow("approved", "user", target, True, f"by-{account}"), hub.viewer())
    revocation = merge_approval.Revocation(target, frozenset({"by-1", "by-2"}), "reopen:x")

    hub.account_id = 1
    with pytest.raises(merge_approval.TrailError, match="承認を書いたアカウント"):
        merge_approval.write_revocations(hub, [revocation], hub.viewer())
    hub.account_id = 2
    merge_approval.write_revocations(hub, [revocation], hub.viewer())

    for account in (1, 2):
        hub.account_id = account
        assert merge_approval.merge_judgment(merge_approval.merge_rows(hub, 7, hub.viewer()), target) is None


def test_a_pr_that_cannot_leave_the_merge_queue_records_nothing_and_a_retry_dequeues(repo, st, env, capsys, monkeypatch):
    tid, target = _approved_design(capsys, repo, st, env)
    before = _events(st, tid)

    def fail(number: int) -> None:
        raise GitHubError("キューを読めない", 502)

    with monkeypatch.context() as m:
        m.setattr(env.hub, "dequeue", fail)
        out = _retry(capsys, tid, DESIGN, "--later", "keep")
    assert out["cause"]["kind"] == "refused" and "キューを読めない" in out["cause"]["detail"], out
    assert _events(st, tid) == before

    # 取り消しの証跡は書けているので、打ち直しは行を増やさずにキューから外す
    comments = len(env.hub.comments_of[target.number])
    out = _retry(capsys, tid, DESIGN, "--later", "keep")
    assert out["status"] == "ok", out
    assert len(env.hub.comments_of[target.number]) == comments and env.hub.dequeued == [target.number]


def test_approvals_before_a_reopen_are_not_read_as_the_current_approval(repo, st, env, capsys):
    tid = _new(capsys)
    lanes.turn(st, tid, repo, env.env)
    target = merge_approval.MergeTarget(merge_hub.REPO, 7, "main", merge_hub.HEAD)
    _approve_production(st, tid, env, target)
    lanes.turn(st, tid, repo, env.env)
    v = run.read(st, tid)
    assert gate.approved_head(v, "design") == merge_hub.HEAD
    assert run.approved_seen(run.StageAnswers(v, DESIGN), st.root)["head"] == merge_hub.HEAD

    _retry(capsys, tid, DESIGN, "--later", "reopen")

    v = run.read(st, tid)
    assert gate.approved_head(v, "design") is None
    assert run.approved_seen(run.StageAnswers(v, DESIGN), st.root) is None
    assert [a["answer"] for a in gate.answers(v)] == ["approved"]
    # 回答の記録は残り、質問は新しい id で出る
    lanes.turn(st, tid, repo, env.env)
    assert gate.question(run.read(st, tid), run.decide(run.read(st, tid))).id != gate.answers(v)[0]["gate_id"]


def test_the_trail_of_an_mvv_verdict_without_a_recorded_answer_is_also_revoked(repo, st, env, capsys):
    """省くと決めた MVV 判定は、回答を記録する前に証跡を PR へ書く。その対象も開き直しで取り消す。"""
    tid = _new(capsys)
    lanes.turn(st, tid, repo, env.env)
    v = run.read(st, tid)
    held = {"gate": "design", "skip": True, "head": merge_hub.HEAD, "target": {"repo": "o/r", "pr": 7, "base": "main", "production": True}}
    held["note_url"] = "https://example.invalid/7#1"
    payload = {"step": "design-mvv", "status": "finished", "report": {"outputs": {"mvv_gate": held}}}
    draft = model.EventDraft("step_recorded", "workflow", DESIGN, payload=payload, rev=v.rev)
    judged = replace(v, events=[*v.events, model.Event(v.events[-1].seq + 1, v.events[-1].at, draft)])

    planned = reopening.plan_reopen(judged, DESIGN, later="keep", reason="r", trigger={"by": "retry"})

    target = merge_approval.MergeTarget("o/r", 7, "main", merge_hub.HEAD)
    assert [(r.target, r.approvals) for r in planned.revoke] == [(target, {held["note_url"]})]


def test_a_reopen_by_hand_does_not_count_as_a_user_choice(repo):
    """取り消しの担い手 `reopen` は却下の行でだけ数え、`--by` で選ぶ値には入れない。"""
    target = merge_approval.MergeTarget("o/r", 7, "main", merge_hub.HEAD)
    hub = merge_hub.MergeHub()
    row = merge_approval.MergeRow("approved", "reopen", target, True, "x")
    hub.rest("POST", "/repos/o/r/issues/7/comments", {"body": merge_approval.render_merge_row(row)})
    assert merge_approval.merge_rows(hub, 7, hub.viewer()) == []
    assert "reopen" not in merge_approval.BY_CHOICES
