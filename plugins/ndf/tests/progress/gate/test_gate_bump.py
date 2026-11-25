"""版上げのスプリント（モード `release-bump`。#4446 の決定 9）: 工程の流れと `ndf progress bump-*`。

作業は偽物に替え、工程が各ステップへ渡す入力を見る。`bump-*` のコマンドは偽の接点（`bump.Io`）で打つ。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from plan_decls import REPO
from plan_decls import decl as _decl
from plan_decls import doc as _doc
from result_json import last_result

from ndf.contracts import result
from ndf.progress import command, gate, invoke, lane, model, plan, run, store
from ndf.progress.gate import bump

DIGEST = "a" * 64


def _material(path: str) -> dict:
    return {"path": path, "sha256": DIGEST, "file_sha256": DIGEST}


@pytest.fixture(autouse=True)
def _same_repo(monkeypatch):
    """計画を作ったリポジトリの識別（`REPO`）を、スプリントと `bump-*` が打つ場所の識別にする。"""
    monkeypatch.setattr(command, "repo_id", lambda cwd, root: REPO)


def _bump_sprint(st, capsys) -> SimpleNamespace:
    """計画のレーン a に溜まったリリースのリクエスト 1 件と、レーン b の版上げのスプリントを作る。"""
    with store.transaction(st) as conn:
        pid = plan.create_plan(conn, REPO, _doc(_decl("a", [[3092]]), _decl("b", [[2]], touches=("src/b",))), True)
        view, _ = lane.read_plan(conn, pid)
        body = {"kind": gate.RELEASE_KIND, "id": "lane-release-x", "lane": "a", "prs": [8]}
        key = plan.open_request(conn, pid, body, "レーン a のリリース", "workflow")
    parent = view.lanes["b"].machine_id
    code = command.main(["new", "bump", "--mode", "release-bump", "--release-requests", key, "--parent", parent])
    out = last_result(capsys.readouterr().out)
    assert code == 0, out
    tid = out["metrics"]["machine_id"]
    with store.transaction(st) as conn:
        lane.start_row(conn, pid, "b", "r1", tid)
    return SimpleNamespace(pid=pid, tid=tid, key=key, name=view.name)


def _fake_work(calls: dict):
    def work(r, cwd, work_dir, answer):
        calls[r.step] = r.inputs
        outputs = {
            "bump-assemble": {"pr": 9, "material": _material("/tmp/assembled.md"), "branch": "release/v1.0.1"},
            "release-reading": {"material": _material("/tmp/read.md")},
            "release-mvv": {"mvv_gate": {"gate": "release", "skip": False, "verdict": None, "why": ["進め方 normal"]}},
            "release": {"product_version": "1.0.1"},
        }.get(r.step)
        return invoke.finished(r, result.result("script", "ok", f"{r.step} を終えた", outputs=outputs))

    return work


@pytest.mark.parametrize(
    "value,recorded,ran_release",
    [("approved", plan.RELEASED, True), ("rejected", plan.REJECTED, False), ("request_changes", plan.REJECTED, False)],
)
def test_bump_sprint_hands_step_outputs_forward_and_records_the_answer(
    st, repo, env, flow, capsys, tmp_path, value, recorded, ran_release
):
    sprint = _bump_sprint(st, capsys)
    calls: dict = {}
    faked = run.Env(**{**vars(env), "invoker": _fake_work(calls), "in_lane": lambda _st, _tid: True})
    assert flow.turn(sprint.tid, faked)["status"] == "gate"
    assert list(calls) == ["bump-assemble", "release-mvv", "release-reading", "bump-present"]
    # 前のステップの出力が後ろのステップの入力になり、後ろの同じキーが上書きする。定義の outputs に無いキーは渡らない
    assert calls["release-reading"]["pr"] == 9 and calls["release-reading"]["material"]["path"] == "/tmp/assembled.md"
    assert calls["bump-present"]["material"]["path"] == "/tmp/read.md" and "branch" not in calls["bump-present"]
    assert calls["bump-present"]["release_requests"] == [sprint.key] and calls["bump-present"]["release_plan"] == sprint.name

    q = gate.waiting(run.read(st, sprint.tid))
    sent = gate.request_release(st, run.read(st, sprint.tid), q, {})
    assert sent == gate.request_release(st, run.read(st, sprint.tid), q, {}) and sent["id"].startswith("bump-release-")
    with store.transaction(st) as conn:
        view, _ = lane.read_plan(conn, sprint.pid)
    req = view.requests[sent["request"]]
    assert req.body["kind"] == "bump-release" and req.body["prs"] == [9] and req.body["material"] == "/tmp/read.md"
    assert req.body["release_requests"] == [sprint.key] and req.body["sprint_state"] == sprint.tid
    # 資料の提示は読み手役の後ろにあるので、確認をやり直すときは両方を打ち直す
    assert run.read(st, sprint.tid).snap.stage("版上げ").from_reading("release") == ["release-reading", "bump-present"]

    notes = tmp_path / "notes.md"
    notes.write_text("直す\n", encoding="utf-8")
    key = f"k-{value}"
    gate.answer(st, sprint.tid, gate.Answer(q.id, value, "回答", "organizer", key, notes if value == "request_changes" else None))
    flow.turn(sprint.tid, faked)
    assert ("release" in calls) is ran_release 
    assert run.decide(run.read(st, sprint.tid)).action == run.Action.DONE
    environ = {invoke.INPUT_ENV: json.dumps(calls["bump-record"])}
    for _ in range(2):  # 打ち直しても、閉じたリクエストを二重に記録しない
        assert bump.main(["bump-record"], environ=environ) == 0, capsys.readouterr().out
    with store.transaction(st) as conn:
        view, _ = lane.read_plan(conn, sprint.pid)
    assert view.requests[sprint.key].release["result"] == recorded
    assert view.requests[sprint.key].release["version"] == ("1.0.1" if ran_release else None)


class _Hub:
    repo = REPO

    def __init__(self) -> None:
        self.posted: list = []

    def rest(self, method, path, payload=None):
        self.posted.append((method, path, payload))

    def pull(self, number):
        return SimpleNamespace(body="承認資料の本文\n", head_ref="sprint/a")


def test_bump_commands_dispatch_once_copy_the_body_and_turn_the_presentation_into_ok(st, repo, capsys):
    sprint = _bump_sprint(st, capsys)
    # 版上げのスプリント以外はリクエストを受けず、版上げのスプリントはリクエストと親のレーンが要る
    for argv in (["--mode", "standard", "--issues", "1", "--release-requests", "q"], ["--mode", "release-bump"]):
        assert command.main(["new", "x", *argv]) != 0
    capsys.readouterr()
    settings = json.loads((Path(__file__).parents[5] / ".ndf" / "project.json").read_text(encoding="utf-8"))
    settings["branch_strategy"]["release"]["cd"] = {"workflow": "cd.yml"}
    (repo / ".ndf").mkdir(exist_ok=True)
    (repo / ".ndf" / "project.json").write_text(json.dumps({"branch_strategy": settings["branch_strategy"]}), encoding="utf-8")
    hub, opened = _Hub(), [[], [], [9]]

    def ndf(argv, cwd):
        if argv[1] == "present":
            return result.result("release-steps", result.GATE, "回答を待つ", routes={"approved": None})
        return result.result("release-steps", result.OK, "開いている PR", [{"kind": "pr", "number": n} for n in opened.pop(0)])

    io = bump.Io(lambda cwd: hub, ndf, sleep=lambda _s: None)
    environ = {invoke.INPUT_ENV: json.dumps({"release_requests": [sprint.key], "release_plan": sprint.name, "pr": 9})}
    assert bump.main(["bump-assemble"], io, environ) == 0
    out = last_result(capsys.readouterr().out)
    assert hub.posted == [("POST", f"/repos/{REPO}/actions/workflows/cd.yml/dispatches", {"ref": "main", "inputs": {"step": "assemble"}})]
    material = out["outputs"]["material"]
    assert out["outputs"]["pr"] == 9 and open(material["path"], encoding="utf-8").read() == "承認資料の本文\n"
    # 打ち直し: 自分が起動して開いた PR は使い、workflow を起動し直さない
    opened.append([9])
    assert bump.main(["bump-assemble"], io, environ) == 0 and len(hub.posted) == 1
    capsys.readouterr()
    environ = {invoke.INPUT_ENV: json.dumps({"pr": 9, "material": material})}
    assert bump.main(["bump-present"], io, environ) == 0
    assert last_result(capsys.readouterr().out)["status"] == "ok"
    assert bump.main(["bump-present"], io, {}) != 0
