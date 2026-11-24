"""承認ゲートの回答の読み口（`ndf.progress.run`・`ndf.progress.gate.records`。#3959 の決定 3）。

リリース承認ゲートを MVV 判定で省かず、レビューの保留を確かめていない `service:mvv` の回答を数えない。更新前の NDF が書いた
記録（判定の `skip: true`・未消費の `service:mvv` の承認・それを消費して済みにした承認ゲート）から再開しても人へ質問する。
作業（プラン）は偽物に差し替え、`next` が起動する作業は同じプロセスで `run.work` として流す。
"""

from __future__ import annotations

import dataclasses
import importlib.util
from pathlib import Path

import pytest
from result_json import last_result

from ndf.contracts import result
from ndf.progress import command, gate, invoke, model, run

ISSUE = 3959
JUDGED = "a" * 40


def _load_lanes():
    """同じ保存先のレーンの部品（lanes.py。偽の作業の結果と 1 手の打ち方）。"""
    spec = importlib.util.spec_from_file_location("lanes", Path(__file__).with_name("lanes.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lanes = _load_lanes()


class Fake:
    """作業の偽物。`script[ステップ]` に積んだ結果を順に返し、無ければ定義した出力を持つ ok を返す。"""

    def __init__(self, repo: Path) -> None:
        self.values = lanes.values(repo, "sprint/s1")
        self.script: dict[str, list] = {}

    def __call__(self, r, cwd, work_dir, answer):
        queued = self.script.get(r.step)
        return queued.pop(0)(r) if queued else lanes.ok_result(r, self.values)


@pytest.fixture
def env(repo):
    fake = Fake(repo)
    e = run.Env(
        facts=lambda words, ctx, cwd: {w: True for w in words},
        launch=lambda st_, tid, cwd: None,
        invoker=fake,
        prepare=lambda r, work_dir, cwd: None,  # 作業が偽物なのでプランの差し込みも組まない
        records=lambda cwd: lanes._NoRecords(),
        sleep=lambda _s: None,
    )
    return dataclasses.make_dataclass("E", ["env", "fake"])(e, fake)


def _new(capsys) -> str:
    code = command.main(["new", "s1", "--mode", "standard", "--issues", str(ISSUE), "--pace", "fast"])
    out = last_result(capsys.readouterr().out)
    assert code == 0, out
    return out["metrics"]["machine_id"]


def _approve(st, tid, stage):
    return lanes.answer(st, tid, stage, "approved", "人が回答した")


OLD_RELEASE = {"gate": "release", "skip": True, "verdict": None, "why": [], "head": JUDGED}


def _held(kind: str, **extra) -> dict:
    """判定のステップの出力 mvv_gate。`extra` に holds を渡さなければ、更新前の NDF が書いた形（holds のキーが無い）。"""
    return {
        "gate": kind,
        "skip": True,
        "verdict": {"verdict": "follow", "reasons": ["Value 1"], "boundary": []},
        "why": [],
        "head": JUDGED,
        **extra,
    }


def _held_step(held: dict):
    return lambda r: invoke.finished(r, result.result("script", "ok", "MVV 判定", outputs={"mvv_gate": held}))


def _answers(st, tid) -> list[model.Event]:
    return [e for e in run.read(st, tid).events if e.draft.kind == "answered"]


def _old_mvv_answer(st, tid, held: dict) -> model.Event:
    """更新前の NDF が書いた、承認ゲートを省いた `service:mvv` の回答（`model.mvv_answer` の形）を、今の待ちへ書く。"""
    v = run.read(st, tid)
    m = run.decide(v)
    assert m.stage is not None and m.step is not None and m.progress is not None
    qid = model.question_id(v.machine_id, v.rev, m.stage.name, m.step.id, m.progress.anchor)
    payload, reason = model.mvv_answer(m.step.id, invoke.LISTEN, run.APPROVED, held, qid)
    run._write(st, tid, model.EventDraft(run.ANSWERED, run.CONDUCTOR, m.stage.name, reason=reason, payload=payload, rev=v.rev))
    return _answers(st, tid)[-1]


def _consumed_by_old_ndf(st, tid, answered: model.Event) -> None:
    """更新前の NDF の `_consume` が、`service:mvv` の回答を読んで承認ゲートのステップを済みにした記録。"""
    v = run.read(st, tid)
    m = run.decide(v)
    assert m.stage is not None and m.step is not None and m.progress is not None
    payload = {"step": m.step.id, "status": run.FINISHED, "report": run.gate_report(m.step, answered), "generation": 0}
    run._write(st, tid, model.EventDraft(run.STEP_EVENT, run.ACTOR, m.stage.name, payload=payload, rev=v.rev))


def _to_release_gate(st, tid, repo, env, release_held: dict) -> dict:
    env.fake.script["release-mvv"] = [_held_step(release_held)]
    lanes.turn(st, tid, repo, env.env)
    _approve(st, tid, "設計")
    return lanes.turn(st, tid, repo, env.env)


def _human(st, tid, head: str = "h1") -> None:
    q = gate.waiting(run.read(st, tid))
    assert q is not None
    gate.answer(st, tid, gate.Answer(q.id, "approved", "人が承認した", "human:reviewer", f"k-{q.id}", seen=gate.Target(head=head)))


def test_a_skip_on_the_release_gate_waits_for_a_human_without_a_service_answer(repo, st, env, capsys):
    tid = _new(capsys)
    out = _to_release_gate(st, tid, repo, env, _held("release", holds=[]))
    assert out["status"] == "gate" and out["metrics"]["step"] == "release-gate"
    assert [e.draft.payload["by"] for e in _answers(st, tid)] == ["human:test"]


def test_an_old_service_answer_on_the_release_gate_asks_a_human_and_the_human_answer_carries_on(repo, st, env, capsys):
    # 決定 3: 更新前の NDF が書いた判定の skip: true と、まだ読まれていない service:mvv の承認から再開する
    tid = _new(capsys)
    _to_release_gate(st, tid, repo, env, OLD_RELEASE)
    old = _old_mvv_answer(st, tid, OLD_RELEASE)
    assert run.decide(run.read(st, tid)).action == run.Action.GATE
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["step"] == "release-gate"
    q = gate.waiting(run.read(st, tid))
    assert q is not None and q.id == old.draft.payload["gate_id"]
    assert gate.approved_head(run.read(st, tid), "release") is None
    _human(st, tid)
    assert run.decide(run.read(st, tid)).action == run.Action.CONSUME
    assert gate.approved_head(run.read(st, tid), "release") == "h1"
    done = lanes.turn(st, tid, repo, env.env)
    assert done["status"] == "ok" and done["metrics"]["state"] == "completed"


def test_a_release_gate_closed_by_an_old_service_answer_is_opened_again(repo, st, env, capsys):
    # 決定 3: 更新前の NDF が service:mvv の回答を消費して済みにした承認ゲートは、後ろのステップが済んでいなければ開き直す
    tid = _new(capsys)
    _to_release_gate(st, tid, repo, env, OLD_RELEASE)
    old = _old_mvv_answer(st, tid, OLD_RELEASE)
    _consumed_by_old_ndf(st, tid, old)
    m = run.decide(run.read(st, tid))
    assert (m.action, m.step.id if m.step else None) == (run.Action.GATE, "release-gate")
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["step"] == "release-gate"
    q = gate.waiting(run.read(st, tid))
    assert q is not None and q.id != old.draft.payload["gate_id"]
    _human(st, tid)
    assert run.decide(run.read(st, tid)).action == run.Action.CONSUME
    done = lanes.turn(st, tid, repo, env.env)
    assert done["status"] == "ok" and done["metrics"]["state"] == "completed"
    assert gate.approved_head(run.read(st, tid), "release") == "h1"
    recorded = [e for e in run.read(st, tid).events if e.draft.kind == "step_recorded" and e.draft.payload["step"] == "release-gate"]
    assert len(recorded) == 2


def test_a_release_gate_closed_by_an_old_service_answer_is_not_opened_after_the_next_step(repo, st, env, capsys):
    tid = _new(capsys)
    _to_release_gate(st, tid, repo, env, OLD_RELEASE)
    old = _old_mvv_answer(st, tid, OLD_RELEASE)
    _consumed_by_old_ndf(st, tid, old)
    v = run.read(st, tid)
    after = {"step": "release", "status": run.FINISHED, "report": {"status": "ok"}, "generation": 0}
    run._write(st, tid, model.EventDraft(run.STEP_EVENT, run.ACTOR, "デプロイ", payload=after, rev=v.rev))
    assert run.decide(run.read(st, tid)).action == run.Action.RESUME


def test_a_failed_merge_after_an_old_service_answer_asks_a_human_after_the_retry(repo, st, env, capsys):
    tid = _new(capsys)
    _to_release_gate(st, tid, repo, env, OLD_RELEASE)
    old = _old_mvv_answer(st, tid, OLD_RELEASE)
    _consumed_by_old_ndf(st, tid, old)
    v = run.read(st, tid)
    run._write(st, tid, run._failed(v.snap.stage("デプロイ"), "release", v.rev, "refused", "承認済みのコミットが無い"))
    out = run.advance(st, tid, repo, env.env)
    assert out["status"] == "stopped"
    assert command.main(["retry", tid, "デプロイ", "--reason", "打ち直す"]) == 0
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["step"] == "release-gate"


def test_the_design_gate_counts_only_a_service_answer_that_checked_the_holds(repo, st, env, capsys):
    # 保留を確かめた証拠（holds: []）を持つ判定だけが設計承認ゲートを省く
    tid = _new(capsys)
    env.fake.script["design-mvv"] = [_held_step(_held("design", holds=[]))]
    lanes.turn(st, tid, repo, env.env)
    assert [e.draft.payload["by"] for e in _answers(st, tid)] == ["service:mvv"]
    assert run.decide(run.read(st, tid)).action == run.Action.CONSUME
    assert gate.approved_head(run.read(st, tid), "design") == JUDGED
    out = lanes.turn(st, tid, repo, env.env)
    assert out["metrics"]["stage"] == "デプロイ"


@pytest.mark.parametrize("extra", [{}, {"holds": None}, {"holds": ["直っていないレッドライン"]}])
def test_the_design_gate_does_not_count_a_verdict_without_checked_holds(repo, st, env, capsys, extra):
    tid = _new(capsys)
    held = _held("design", **extra, **FREE)
    env.fake.script["design-mvv"] = [_held_step(held)]
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["step"] == "design-gate" and _answers(st, tid) == []
    # 閲覧範囲を限れる保存先は、判定の自由記述を伏せずに残す
    assert st.private is True and _recorded_mvv(st, tid, "design-mvv")["verdict"]["reasons"] == ["理由 1", "理由 2"]
    # 更新前の NDF が書いた、まだ読まれていない service:mvv の回答も数えない
    _old_mvv_answer(st, tid, held)
    assert run.decide(run.read(st, tid)).action == run.Action.GATE
    assert gate.approved_head(run.read(st, tid), "design") is None


def test_consume_does_not_take_an_answer_that_is_not_counted(repo, st, env, capsys):
    tid = _new(capsys)
    _to_release_gate(st, tid, repo, env, OLD_RELEASE)
    old = _old_mvv_answer(st, tid, OLD_RELEASE)
    v = run.read(st, tid)
    m = run.decide(v)
    d = run.Drive(st, tid, repo, env.env, inline=True)
    before = len(v.events)
    out = run._consume(d, v, dataclasses.replace(m, action=run.Action.CONSUME, event=old))
    assert out is not None and out["status"] == "gate" and len(run.read(st, tid).events) == before
    # 回答が無く、直前の判定が省かないと決めた手でも service:mvv の回答を書かない
    out = run._consume(d, v, dataclasses.replace(m, action=run.Action.CONSUME, event=None))
    assert out is not None and out["status"] == "gate" and len(run.read(st, tid).events) == before


@pytest.mark.parametrize(
    ("payload", "kind", "void"),
    [
        ({"by": "service:mvv", "mvv_gate": {"holds": []}}, "release", True),
        ({"by": "service:mvv", "mvv_gate": {"holds": []}}, None, True),
        ({"by": "service:mvv", "mvv_gate": {"holds": []}}, "design", False),
        ({"by": "service:mvv", "mvv_gate": {}}, "design", True),
        ({"by": "service:mvv", "mvv_gate": {"holds": None}}, "design", True),
        ({"by": "service:mvv", "mvv_gate": None}, "design", True),
        ({"by": "service:mvv"}, "design", True),
        ({"by": "human:a", "mvv_gate": {}}, "release", False),
        ({"by": "conductor"}, "release", False),
        ({}, "release", False),
        ({"by": 3}, "release", False),
    ],
)
def test_mvv_answer_void(payload, kind, void):
    assert model.mvv_answer_void(payload, kind) is void


def test_mvv_skip_takes_only_a_design_verdict_that_checked_the_holds():
    done = {"m": {"outputs": {"mvv_gate": _held("design", holds=[])}}}
    assert model.mvv_skip(done, "m", "design") is not None
    for held in (_held("design"), _held("design", holds=None), _held("design", holds=["x"])):
        assert model.mvv_skip({"m": {"outputs": {"mvv_gate": held}}}, "m", "design") is None
    assert model.mvv_skip({"m": {"outputs": {"mvv_gate": _held("release", holds=[])}}}, "m", "release") is None
    assert model.hold_checked({"holds": []}) and not model.hold_checked({"holds": ()}) and not model.hold_checked(None)


# --- 閲覧範囲を限れない保存先へ書く判定（#3959 の「手元のファイルの閲覧範囲」） ------------------------------------

FREE = {
    "verdict": {"verdict": "follow", "reasons": ["理由 1", "理由 2"], "boundary": ["線 1"], "basis": ["Value 1"]},
    "why": ["省かない 1"],
}


def _recorded_mvv(st, tid, step: str) -> dict:
    ev = [e for e in run.read(st, tid).events if e.draft.kind == "step_recorded" and e.draft.payload["step"] == step][-1]
    return ev.draft.payload["report"]["outputs"]["mvv_gate"]


def test_an_unrestricted_store_withholds_the_free_text_and_still_counts_the_answer(repo, st, env, capsys):
    from ndf.progress import stages, store

    st.private = False
    tid = _new(capsys)
    env.fake.script["design-mvv"] = [_held_step(_held("design", holds=[], **FREE))]
    lanes.turn(st, tid, repo, env.env)
    stored = _recorded_mvv(st, tid, "design-mvv")
    assert stored["verdict"]["reasons"] == [model.MVV_WITHHELD] * 2 and stored["why"] == [model.MVV_WITHHELD] and stored["holds"] == []
    (ans,) = _answers(st, tid)
    p = ans.draft.payload
    assert p["mvv_gate"]["verdict"]["boundary"] == [model.MVV_WITHHELD] and p["mvv_gate"]["verdict"]["basis"] == ["Value 1"]
    assert (p["by"], p["answer"], p["gate"], p["seen"]["head"]) == ("service:mvv", "approved", "design", JUDGED)
    assert not model.mvv_answer_void(p, "design") and gate.approved_head(run.read(st, tid), "design") == JUDGED
    # 保留 1 件を写した回答は、伏せても保留 1 件のまま数えない
    held = _held("design", holds=["直っていないレッドライン"])
    assert model.mvv_answer_void(model.mvv_answer("design-gate", "listen", "approved", model.mvv_stored_view(held), "x")[0], "design")
    # 閲覧範囲の値（conn.info）を持たない接続で書いた工程の出力も伏せる
    v = run.read(st, tid)
    draft = model.EventDraft(
        stages.OUTPUTS_EVENT, run.CONDUCTOR, "設計", payload={"outputs": {"mvv_gate": _held("design", **FREE)}}, rev=v.rev
    )
    with st.engine.begin() as conn:
        store.append_events(conn, tid, [draft], v.events[-1].seq)
    got = run.read(st, tid).events[-1].draft.payload["outputs"]["mvv_gate"]
    assert got["verdict"]["reasons"] == [model.MVV_WITHHELD] * 2 and got["why"] == [model.MVV_WITHHELD] and got["head"] == JUDGED
