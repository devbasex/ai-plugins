"""待っていない承認ゲートの工程にも回答を記録し、否定の回答で工程を開き直すこと（#4520 の受け入れ条件 5〜9・14・15・18・21・22）。

ステートマシンの DB は本物で、作業（プラン）と GitHub は偽物にする（偽物は `test_manual_gate` と共有する）。
"""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

import merge_hub
import pytest
from result_json import last_result

from ndf import contracts
from ndf.adapters.github import Authored
from ndf.contracts import result
from ndf.core import merge_approval
from ndf.progress import approval_decide, command, gate, invoke, model, run, store
from ndf.progress.gate import answering, entry, finishing


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manual = _load("test_manual_gate")
env = manual.env
DESIGN, DETAIL, DESIGN_PR = manual.DESIGN, manual.DETAIL, manual.DESIGN_PR


class Records:
    def __init__(self) -> None:
        self.bodies: dict[int, str] = {}

    def read(self, number: int) -> str:
        return self.bodies.get(number, "本文\n")

    def write(self, number: int, body: str) -> None:
        self.bodies[number] = body


@pytest.fixture
def board(env, monkeypatch):
    """`ndf progress question` と `ndf answer` のエントリポイントが使う GitHub と進捗記録を偽物にする。"""
    records = Records()
    env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, ref="sprint/s1")
    monkeypatch.setattr(entry, "shared_hub", lambda cwd: lambda: env.hub)
    monkeypatch.setattr(entry, "github_records", lambda cwd: records)
    monkeypatch.setattr(finishing, "github_records", lambda cwd: records)
    return records


def _question(capsys, tid: str, stage: str, *extra: str) -> dict:
    finishing.main(["question", tid, stage, *extra])
    return last_result(capsys.readouterr().out)


def _give(capsys, repo, shown: dict, value: str, key: str, by: str = "human:reviewer") -> dict:
    """質問の routes のコマンドを、そのまま `ndf answer` のエントリポイントで打つ。"""
    argv = list(shown["routes"][value]["argv"][2:])
    argv += ["--reason", f"{value} の理由", "--by", by, "--key", key]
    if value == "request_changes":
        notes = repo.parent / "notes.md"
        notes.write_text("直して\n")
        argv += ["--notes", str(notes)]
    entry.main(argv)
    return last_result(capsys.readouterr().out)


def _write(st, tid: str, stage: str, kind: str, **payload) -> None:
    actor = "workflow"
    run._write(st, tid, model.EventDraft(kind, actor, stage, payload=payload or None, rev=1))


def _in_state(st, capsys, repo, env, state: str, mode: str = "standard") -> str:
    """設計の工程が `state` のステートマシン。"""
    tid = manual._new(capsys, mode)
    manual._before_design(capsys, repo, tid)
    if state in ("running", "failed"):
        _write(st, tid, DESIGN, "started")
    if state == "failed":
        _write(st, tid, DESIGN, "failed", step=None)
    if state == "finished":
        assert manual._finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})["status"] == "ok"
        shown = manual._next(st, tid, repo, env)
        assert manual._answer(st, tid, repo, env, shown)["status"] == "ok"
        env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, ref="sprint/s1", merged=True)
        run._write(st, tid, model.EventDraft("finished", "workflow", DESIGN, payload={}, rev=1))
    assert manual._state(st, tid, DESIGN) == state
    return tid


def _pr_args(state: str) -> list[str]:
    return [] if state == "finished" else ["--pr", str(DESIGN_PR)]


@pytest.mark.parametrize("value", ["approved", "request_changes"])
@pytest.mark.parametrize("state", ["pending", "running", "failed", "finished"])
def test_an_answer_is_recorded_in_any_state(repo, st, env, board, capsys, state, value):
    """受け入れ条件 5。"""
    tid = _in_state(st, capsys, repo, env, state)

    shown = _question(capsys, tid, DESIGN, *_pr_args(state))
    assert shown["status"] == "gate" and shown["metrics"]["gate"]["pr"] == DESIGN_PR, shown
    out = _give(capsys, repo, shown, value, f"{state}-{value}")

    assert out["status"] == "ok", out
    assert gate.answers(run.read(st, tid))[-1]["answer"] == value
    if value == "approved":
        assert manual._state(st, tid, DESIGN) == state
        assert run.read(st, tid).context()["design_pr"] == DESIGN_PR
        assert gate.approved_head(run.read(st, tid), "design", st.root) == merge_hub.HEAD


def _finished_with_later(st, capsys, repo, env) -> str:
    """設計を承認して終え、後ろの工程を流し終えたステートマシン（リリース承認ゲートの手前まで）。"""
    tid = manual._held_design(st, capsys, repo, env)
    shown = manual._next(st, tid, repo, env)
    manual._answer(st, tid, repo, env, shown)
    env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, ref="sprint/s1", merged=True)
    manual._next(st, tid, repo, env)
    assert manual._state(st, tid, DESIGN) == "finished" and manual._state(st, tid, DETAIL) == "finished"
    return tid


def test_a_change_request_after_the_approval_reopens_the_stage_and_the_later_ones(repo, st, env, board, capsys):
    """受け入れ条件 6・14・15・18。"""
    tid = _finished_with_later(st, capsys, repo, env)
    shown = _question(capsys, tid, DESIGN)
    out = _give(capsys, repo, shown, "request_changes", "k-change")

    assert out["status"] == "ok", out
    names = [i["stage"] for i in out["items"]]
    assert names[0] == DESIGN and DETAIL in names
    assert manual._state(st, tid, DESIGN) == "pending" and manual._state(st, tid, DETAIL) == "pending"
    assert any(DESIGN in w and "取り消されない" in w for w in out["warnings"]), out
    assert "- [ ] 設計\n" in board.bodies[manual.ISSUE] and "- [x] 要求と受け入れ条件" in board.bodies[manual.ISSUE]
    assert run.step_progress(run.read(st, tid), DESIGN).notes == "直して\n"
    assert run.step_progress(run.read(st, tid), DETAIL).notes is None
    # 前の承認は読まない
    assert gate.approved_head(run.read(st, tid), "design", st.root) is None

    env.calls.clear()
    env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, ref="sprint/s1")
    again = manual._next(st, tid, repo, env)

    assert env.calls[0] == "design" and again["status"] == "gate" and again["metrics"]["stage"] == DESIGN, again
    assert manual._state(st, tid, DETAIL) == "pending"


def test_a_rejection_of_a_finished_stage_fails_it_and_reopens_the_later_ones(repo, st, env, board, capsys):
    """受け入れ条件 6・7。"""
    tid = _finished_with_later(st, capsys, repo, env)

    out = _give(capsys, repo, _question(capsys, tid, DESIGN), "rejected", "k-reject")

    assert out["status"] == "ok", out
    assert manual._state(st, tid, DESIGN) == "failed" and manual._state(st, tid, DETAIL) == "pending"
    env.calls.clear()
    after = manual._next(st, tid, repo, env)
    assert after["status"] == "stopped" and {"retry", "finish", "cancel"} <= set(after["routes"]), after
    assert env.calls == [] and manual._state(st, tid, DETAIL) == "pending"


def test_the_trail_of_the_approval_is_revoked_when_an_answer_reopens_the_stage(repo, st, env, board, capsys):
    tid = _finished_with_later(st, capsys, repo, env)
    shown = _question(capsys, tid, DESIGN)
    before = len(env.hub.comments_of.get(DESIGN_PR, []))

    assert _give(capsys, repo, shown, "request_changes", "k-revoke")["status"] == "ok"

    bodies = [c.body for c in env.hub.comments_of[DESIGN_PR][before:]]
    assert bodies and "rejected" in bodies[0]
    assert DESIGN_PR in env.hub.dequeued


@pytest.mark.parametrize("pace", ["normal", "auto"])
def test_a_change_request_of_a_pending_stage_keeps_the_later_stages_waiting(repo, st, env, board, capsys, pace):
    """受け入れ条件 7。`auto` でも MVV 判定で省かず人へ問う。"""
    command.main(["new", "s1", "--mode", "standard", "--issues", str(manual.ISSUE), "--pace", pace])
    tid = last_result(capsys.readouterr().out)["metrics"]["machine_id"]
    values = manual.lanes.values(repo, "sprint/s1")
    if pace == "auto":
        skip = {"gate": "design", "skip": True, "verdict": None, "why": [], "holds": [], "head": merge_hub.HEAD}
        values = {**values, "mvv_gate": skip}
    manual._before_design(capsys, repo, tid)
    shown = _question(capsys, tid, DESIGN, "--pr", str(DESIGN_PR))
    assert _give(capsys, repo, shown, "request_changes", "k-pending")["status"] == "ok"
    judged: list[list[str]] = []

    def judge(r):
        judged.append(list(r.args["argv"]))
        return manual.lanes.ok_result(r, values)

    env.script["design-mvv"] = [judge]

    out = manual._next(st, tid, repo, env)

    assert out["status"] == "gate" and out["metrics"]["stage"] == DESIGN, out
    # 判定は承認の証跡を書くので、否定の回答が残る間は省かない指定で打つ
    assert [argv[-1] for argv in judged] == ["--no-skip"], judged
    assert manual._state(st, tid, DETAIL) == "pending"


def test_an_early_approval_is_used_by_the_flow_without_asking_again(repo, st, env, board, capsys):
    tid = _in_state(st, capsys, repo, env, "pending")
    assert _give(capsys, repo, _question(capsys, tid, DESIGN, "--pr", str(DESIGN_PR)), "approved", "k-early")["status"] == "ok"

    out = manual._next(st, tid, repo, env)

    assert "design-gate" not in env.calls and manual._state(st, tid, DETAIL) == "finished", out


def test_a_stage_without_a_gate_has_no_question(repo, st, env, board, capsys):
    """受け入れ条件 8・21。"""
    tid = manual._new(capsys)

    out = _question(capsys, tid, "要求と受け入れ条件")

    assert out["status"] == "stopped" and out["cause"]["kind"] == "refused", out
    assert "承認ゲートを持たない" in out["cause"]["detail"] and "ndf progress show" in out["cause"]["detail"]


@pytest.mark.parametrize(
    ("stage", "extra", "needle"),
    [(DESIGN, [], "--pr"), (DESIGN, ["--pr", "8"], "ブランチ"), ("デプロイ", ["--pr", "8"], "--pr")],
)
def test_a_question_is_refused_with_the_right_way_to_ask(repo, st, env, board, capsys, stage, extra, needle):
    env.hub.pulls_of[8] = merge_hub.pull(8, ref="another")
    tid = manual._new(capsys)
    manual._before_design(capsys, repo, tid)

    out = _question(capsys, tid, stage, *extra)

    assert out["status"] == "stopped" and needle in out["cause"]["detail"], out


def test_a_waiting_stage_returns_its_question(repo, st, env, board, capsys):
    tid = manual._held_design(st, capsys, repo, env)
    shown = manual._next(st, tid, repo, env)

    out = _question(capsys, tid, DESIGN)

    assert out["status"] == "gate" and out["metrics"]["gate"]["id"] == shown["metrics"]["gate"]["id"], out
    schema = contracts.load_json("schemas/io/progress.result.schema.json")
    assert out["tool"] == schema["properties"]["tool"]["const"]
    assert set(out["metrics"]) <= set(schema["properties"]["metrics"]["properties"]), out
    assert out["routes"]["approved"]["argv"][1] == "answer", out


def test_a_waiting_gate_without_a_recorded_pr_uses_the_given_pr(repo, st, env, board, capsys):
    """設計 PR の記録が無い待ちでは、`--pr` の PR を提示し、回答の受理と承認の記録でも同じ PR を使う。"""
    values = manual.lanes.values(repo, "sprint/s1")
    del values["design_pr"]

    def without_pr(r):
        outputs = {k: values[k] for k in r.outputs if k in values}
        return invoke.finished(r, result.result("plan", "ok", f"{r.step} を終えた", outputs=outputs))

    for step in ("design", "design-review", "design-mvv", "design-reading"):
        env.script[step] = [without_pr]
    tid = manual._new(capsys)
    manual._before_design(capsys, repo, tid)
    assert manual._next(st, tid, repo, env)["metrics"]["gate"]["pr"] is None

    shown = _question(capsys, tid, DESIGN, "--pr", str(DESIGN_PR))
    assert shown["status"] == "gate" and shown["metrics"]["gate"]["pr"] == DESIGN_PR, shown
    assert manual._next(st, tid, repo, env)["metrics"]["gate"]["pr"] == DESIGN_PR
    out = _give(capsys, repo, shown, "approved", "k-given")

    assert out["status"] == "ok", out
    assert run.read(st, tid).context()["design_pr"] == DESIGN_PR
    assert env.hub.comments_of[DESIGN_PR]


def test_a_late_answer_is_checked_like_a_waiting_one(repo, st, env, board, capsys):
    """受け入れ条件 9。"""
    tid = _in_state(st, capsys, repo, env, "pending")
    shown = _question(capsys, tid, DESIGN, "--pr", str(DESIGN_PR))
    before = len(run.read(st, tid).events)

    ai = _give(capsys, repo, shown, "approved", "k-ai", by="conductor")
    assert ai["status"] == "stopped" and "--by human" in ai["cause"]["detail"], ai
    env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, ref="sprint/s1", head=merge_hub.OTHER)
    moved = _give(capsys, repo, shown, "approved", "k-moved")
    assert moved["status"] == "stopped" and moved["cause"]["kind"] == "refused", moved
    assert len(run.read(st, tid).events) == before and not env.hub.comments_of.get(DESIGN_PR)

    env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, ref="sprint/s1")
    assert _give(capsys, repo, shown, "approved", "k-same")["status"] == "ok"
    count, comments = len(run.read(st, tid).events), len(env.hub.comments_of[DESIGN_PR])
    again = _give(capsys, repo, shown, "approved", "k-same")

    assert again["metrics"]["replayed"] is True, again
    assert len(run.read(st, tid).events) == count and len(env.hub.comments_of[DESIGN_PR]) == comments


def test_the_work_lock_is_taken_again_when_the_answer_turns_late(repo, st, env, board, capsys, monkeypatch):
    """承認でない回答が待ちへの回答でないと分かったら、作業が流れている間は何も書かずに断る。"""
    tid = _finished_with_later(st, capsys, repo, env)
    shown = _question(capsys, tid, DESIGN)
    before = len(run.read(st, tid).events)

    with store.run_lock(st, tid):
        out = _give(capsys, repo, shown, "request_changes", "k-busy")

    assert out["status"] == "stopped" and "ndf progress wait" in out["cause"]["detail"], out
    assert len(run.read(st, tid).events) == before


def test_a_question_made_before_another_record_is_refused(repo, st, env, board, capsys):
    tid = _in_state(st, capsys, repo, env, "pending")
    shown = _question(capsys, tid, DESIGN, "--pr", str(DESIGN_PR))
    _write(st, tid, DESIGN, "started")

    out = _give(capsys, repo, shown, "approved", "k-stale")

    assert out["status"] == "stopped" and "ndf progress question" in out["cause"]["detail"], out


def test_a_failed_commit_after_the_trail_can_be_sent_again(repo, st, env, board, capsys, monkeypatch):
    """証跡を投稿した後に確定が失敗しても、同じ質問の id へ打ち直せて、証跡は増えない。"""
    tid = _finished_with_later(st, capsys, repo, env)
    shown = _question(capsys, tid, DESIGN)
    commit, broken = answering._commit_answer, [True]

    def flaky(*args, **kwargs):
        if broken:
            broken.pop()
            raise store.Conflict("ぶつかった")
        return commit(*args, **kwargs)

    monkeypatch.setattr(answering, "_commit_answer", flaky)
    assert _give(capsys, repo, shown, "request_changes", "k-flaky")["status"] == "stopped"
    comments = len(env.hub.comments_of[DESIGN_PR])

    out = _give(capsys, repo, shown, "request_changes", "k-flaky")

    assert out["status"] == "ok" and manual._state(st, tid, DESIGN) == "pending", out
    assert len(env.hub.comments_of[DESIGN_PR]) == comments


def test_a_change_request_is_refused_while_work_is_running(repo, st, env, board, capsys):
    tid = _in_state(st, capsys, repo, env, "running")
    shown = _question(capsys, tid, DESIGN, "--pr", str(DESIGN_PR))
    before = len(run.read(st, tid).events)

    with store.run_lock(st, tid):
        out = _give(capsys, repo, shown, "request_changes", "k-running")

    assert out["status"] == "stopped" and "ndf progress wait" in out["cause"]["detail"], out
    assert len(run.read(st, tid).events) == before and not env.hub.comments_of.get(DESIGN_PR)


@pytest.mark.parametrize(("state", "needle"), [("finished", "ndf progress retry"), ("running", "ndf progress wait")])
def test_a_refused_finish_names_another_command(repo, st, env, board, capsys, state, needle):
    """受け入れ条件 21。"""
    tid = _in_state(st, capsys, repo, env, state)

    out = manual._finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})

    assert out["status"] == "stopped" and needle in out["cause"]["detail"], out


def test_an_answer_with_an_unknown_question_names_another_command(repo, st, env, board, capsys):
    tid = _in_state(st, capsys, repo, env, "pending")
    entry.main([tid, "--gate", "no-such", "--result", "approved", "--reason", "r", "--by", "human", "--key", "k-unknown"])

    out = last_result(capsys.readouterr().out)

    assert out["status"] == "stopped" and "ndf progress question" in out["cause"]["detail"], out


def test_the_2026_11_25_sequence_can_be_recorded(repo, st, env, board, capsys):
    """受け入れ条件 22: 手で終える → 変更要求 → 直して手で終える → 承認を求められる → 承認 → 次の工程。"""
    tid = manual._held_design(st, capsys, repo, env)
    shown = manual._next(st, tid, repo, env)
    assert _give(capsys, repo, shown, "request_changes", "e2e-1")["status"] == "ok"
    assert manual._state(st, tid, DESIGN) == "pending"

    env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, ref="sprint/s1", head=merge_hub.OTHER)
    assert manual._finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})["status"] == "ok"
    asked = manual._next(st, tid, repo, env)
    assert asked["status"] == "gate" and asked["metrics"]["gate"]["head"] == merge_hub.OTHER, asked
    assert manual._state(st, tid, DETAIL) == "pending"

    assert _give(capsys, repo, asked, "approved", "e2e-2")["status"] == "ok"
    env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, ref="sprint/s1", head=merge_hub.OTHER, merged=True)
    env.calls.clear()
    manual._next(st, tid, repo, env)

    assert manual._state(st, tid, DESIGN) == "finished" and manual._state(st, tid, DETAIL) == "finished" and env.calls
    assert [a["answer"] for a in gate.answers(run.read(st, tid))] == ["request_changes", "approved"]


def test_a_change_request_after_the_gate_passed_in_a_failed_stage_asks_again(repo, st, env, board, capsys):
    """承認ゲートを通った後で失敗した工程は、変更要求の後に `retry` しても先へ進まない（受け入れ条件 7）。"""
    tid = manual._approved_in_the_flow(st, capsys, repo, env)
    assert _give(capsys, repo, _question(capsys, tid, DESIGN), "request_changes", "k-failed")["status"] == "ok"
    finishing.main(["retry", tid, DESIGN, "--reason", "直した"])
    capsys.readouterr()

    out = manual._next(st, tid, repo, env)

    assert out["status"] == "gate" and out["metrics"]["stage"] == DESIGN, out
    assert manual._state(st, tid, DETAIL) == "pending"


def test_an_objection_survives_a_reopen_and_the_mvv_verdict_does_not_skip(repo, st, env, board, capsys):
    """否定の回答は開き直しの後も残り、`auto` でも承認ゲートを MVV 判定で省かない。"""
    command.main(["new", "s1", "--mode", "standard", "--issues", str(manual.ISSUE), "--pace", "auto"])
    tid = last_result(capsys.readouterr().out)["metrics"]["machine_id"]
    skip = {"gate": "design", "skip": True, "verdict": None, "why": [], "holds": [], "head": merge_hub.HEAD}
    values = {**manual.lanes.values(repo, "sprint/s1"), "mvv_gate": skip}
    env.script["design-mvv"] = [lambda r: manual.lanes.ok_result(r, values)]
    manual._before_design(capsys, repo, tid)
    shown = _question(capsys, tid, DESIGN, "--pr", str(DESIGN_PR))
    assert _give(capsys, repo, shown, "request_changes", "k-objection")["status"] == "ok"
    finishing.main(["retry", tid, "作業場所の用意", "--reason", "作り直す", "--later", "reopen"])
    assert last_result(capsys.readouterr().out)["status"] == "ok"
    assert manual._finish(capsys, repo, tid, "作業場所の用意", {"worktree": str(repo), "branch": "sprint/s1"})["status"] == "ok"

    out = manual._next(st, tid, repo, env)

    assert "design-mvv" in env.calls
    assert out["status"] == "gate" and out["metrics"]["stage"] == DESIGN, out
    assert manual._state(st, tid, DETAIL) == "pending"


def _approved_by_mvv(repo, st, env, capsys) -> str:
    """`auto` のスプリントで、MVV 判定が省くと決め、`service:mvv` の承認を書いた直後（確定の前）のステートマシン。"""
    command.main(["new", "s1", "--mode", "standard", "--issues", str(manual.ISSUE), "--pace", "auto"])
    tid = last_result(capsys.readouterr().out)["metrics"]["machine_id"]
    held = {"gate": "design", "skip": True, "verdict": None, "why": [], "holds": [], "pr": DESIGN_PR, "head": merge_hub.HEAD}
    values = {**manual.lanes.values(repo, "sprint/s1"), "mvv_gate": held}
    env.script["design-mvv"] = [lambda r: manual.lanes.ok_result(r, values)]
    manual._before_design(capsys, repo, tid)
    manual._next(st, tid, repo, env)
    assert run.decide(run.read(st, tid)).action == run.Action.CONSUME
    return tid


def test_the_mvv_trail_is_written_when_the_flow_confirms_the_approval(repo, st, env, board, capsys):
    tid = _approved_by_mvv(repo, st, env, capsys)
    written: list[str] = []
    env.env = replace(env.env, mvv_trail=lambda answered: written.append(answered["by"]))
    assert written == []

    manual._next(st, tid, repo, env)

    assert written == [model.MVV_SERVICE]


def test_an_objection_after_the_judgement_stops_the_mvv_approval(repo, st, env, board, capsys):
    """判定の後に変更要求が確定したら、MVV の承認の証跡を書かず、人へ問う。"""
    tid = _approved_by_mvv(repo, st, env, capsys)
    written: list[str] = []
    env.env = replace(env.env, mvv_trail=lambda answered: written.append(answered["by"]))
    payload = {"kind": invoke.LISTEN, "gate": "design", "step": "design-gate", "answer": "request_changes", "late": True}
    run._write(st, tid, model.EventDraft("answered", "conductor", DESIGN, reason="直して", payload=payload, rev=1))

    out = manual._next(st, tid, repo, env)

    assert written == [] and out["status"] == "gate" and out["metrics"]["stage"] == DESIGN, out


def test_a_rejection_in_the_pr_trail_stops_the_mvv_approval(repo, st, env, board, capsys):
    """ステートマシンの記録に無い利用者の却下が PR にあれば、承認ゲートを済ませず人へ問う。"""
    tid = _approved_by_mvv(repo, st, env, capsys)
    withheld = approval_decide.Withheld("利用者の却下がある", DESIGN_PR, frozenset())
    revoked: list = []
    env.env = replace(env.env, mvv_trail=lambda _answered: withheld, revoke=revoked.extend)

    out = manual._next(st, tid, repo, env)

    assert out["status"] == "gate" and out["metrics"]["stage"] == DESIGN, out
    assert [r for r in revoked if r.target.number == DESIGN_PR] == []  # 投稿していない承認は取り消さない


def test_an_mvv_row_is_not_written_over_a_user_rejection():
    """利用者の却下の後に、同じ対象へ利用者でない担い手の承認を書かない。"""
    hub = merge_hub.MergeHub()
    target = merge_approval.MergeTarget(merge_hub.REPO, DESIGN_PR, "main", merge_hub.HEAD)
    rejected = merge_approval.MergeRow("rejected", "user", target, True, "plan:answer:1")
    merge_approval.write_merge_row(hub, rejected, hub.viewer())
    approved = merge_approval.MergeRow("approved", "mvv", target, True, "mvv:1")

    with pytest.raises(merge_approval.UserRejected):
        merge_approval.write_merge_row(
            hub, approved, hub.viewer(), preface=f"{merge_approval.MVV_NOTE_HEADING}design）\n\n", yield_to_user=True
        )

    assert len(hub.comments(DESIGN_PR)) == 1


@pytest.mark.parametrize(("account", "association", "stops"), [(1, "OWNER", True), (2, "COLLABORATOR", True), (2, "NONE", False)])
def test_an_mvv_approval_posted_after_a_user_rejection_does_not_merge(account, association, stops):
    """投稿と重なって却下の後に載った MVV の承認は、別のアカウントの却下でも数えない。外部の人の却下は数えない。"""
    hub = merge_hub.MergeHub()
    target = merge_approval.MergeTarget(merge_hub.REPO, DESIGN_PR, "main", merge_hub.HEAD)
    rejected = merge_approval.render_merge_row(merge_approval.MergeRow("rejected", "user", target, True, "plan:answer:1"))
    approved = merge_approval.render_merge_row(merge_approval.MergeRow("approved", "mvv", target, True, "mvv:1"))
    hub.comments_of[DESIGN_PR] = [
        Authored("someone", association, rejected, author_id=account),
        Authored(hub.login, "OWNER", f"{merge_approval.MVV_NOTE_HEADING}design）\n\n{approved}", author_id=hub.account_id),
    ]

    found = merge_approval.merge_judgment(merge_approval.merge_rows(hub, DESIGN_PR, hub.viewer()), target)

    assert (found is None) is stops
