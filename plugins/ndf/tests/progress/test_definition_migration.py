"""このリリースの前に始めた状態機械の写しの移行（クリーンアップ → デプロイの順をデプロイ → クリーンアップへ。#4409 の決定 6）。"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

import pytest

from ndf.contracts import result
from ndf.progress import command, definition, invoke, run, run_view, stages

MODE = "light"


def _lanes():
    spec = importlib.util.spec_from_file_location("lanes", Path(__file__).parent / "lanes.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lanes = _lanes()


def _old_snapshot() -> stages.Snapshot:
    """クリーンアップがデプロイより前にある、このリリースの前の写し。"""
    snap = definition.load_definition(MODE)
    ids = [s.id for s in snap.stages]
    order = list(snap.stages)
    cleanup = order.pop(ids.index("cleanup"))
    order.insert([s.id for s in order].index("deploy"), cleanup)
    return replace(snap, stages=tuple(order))


def _env(repo: Path, failing: set[str]) -> run.Env:
    values = lanes.values(repo, "sprint/s1")

    def invoker(r, cwd, work_dir, answer):
        if r.name in failing:
            out = result.stopped("plan", "precondition", "sprint/s1 のマージ済みの PR が無い")
            return invoke.finished(r, out)
        return lanes.ok_result(r, values)

    return run.Env(
        facts=lambda words, ctx, cwd: {w: True for w in words},
        launch=lambda st_, tid, cwd: None,
        invoker=invoker,
        prepare=lambda r, work_dir, cwd: None,
        records=lambda cwd: lanes._NoRecords(),
        sleep=lambda _s: None,
    )


def _new_old(capsys, monkeypatch) -> str:
    old = _old_snapshot()
    with monkeypatch.context() as m:
        m.setattr(definition, "load_definition", lambda mode: old)
        code = command.main(["new", "s1", "--mode", MODE, "--issues", "4409"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0, out
    return out["metrics"]["machine_id"]


def _migrations(st, tid: str) -> int:
    return sum(1 for e in run.read(st, tid).events if e.draft.kind == run_view.DEFINITION_MIGRATED)


def _ids(st, tid: str) -> list[str]:
    return [s.id for s in run.read(st, tid).snap.stages]


def _until_release_gate(st, tid: str, repo: Path, env: run.Env) -> dict:
    """承認ゲートには利用者として承認で回答しながら、リリース承認ゲートの待ちまで流す。"""
    for _ in range(60):
        out = lanes.turn(st, tid, repo, env)
        if out["status"] != "gate":
            assert out["status"] == "ok", out
            continue
        v = run.read(st, tid)
        m = run.decide(v)
        if m.step is not None and m.step.gate == "release":
            return out
        lanes.answer(st, tid, out["metrics"]["stage"], "approved", "承認")
    pytest.fail("リリース承認ゲートまで進まない")


def _no_migration(monkeypatch):
    """移行の無い前のバージョンの振る舞い（`next` が移行を打たない）。"""
    monkeypatch.setattr(run, "migrate_definition", lambda st, machine_id: None)


def test_an_old_snapshot_is_migrated_once_and_reaches_the_release_gate(repo, st, capsys, monkeypatch):
    tid = _new_old(capsys, monkeypatch)
    assert _ids(st, tid).index("cleanup") < _ids(st, tid).index("deploy")
    _until_release_gate(st, tid, repo, _env(repo, set()))
    assert _migrations(st, tid) == 1
    ids = _ids(st, tid)
    assert ids.index("cleanup") == ids.index("deploy") + 1
    # 写しそのもの（由来）は書き換えない
    v = run.read(st, tid)
    assert v.snap.sha256 == _old_snapshot().sha256
    # クリーンアップはデプロイの承認を待つ間に流れていない
    assert v.rows[v.snap.stages[ids.index("cleanup")].name].state != run.FINISHED
    # 打ち直しても移行の事象は 1 件のまま
    assert run.migrate_definition(st, tid) is None and _migrations(st, tid) == 1


def test_a_cleanup_stopped_on_an_open_pr_is_migrated_and_waits_for_deploy(repo, st, capsys, monkeypatch):
    """旧版のクリーンアップが開いた PR で precondition で止まった状態機械も、next で移行されてデプロイへ進む。"""
    tid = _new_old(capsys, monkeypatch)
    with monkeypatch.context() as m:
        _no_migration(m)
        for _ in range(60):
            out = lanes.turn(st, tid, repo, _env(repo, {"merged"}))
            if out["status"] == "stopped":
                break
            if out["status"] == "gate":
                lanes.answer(st, tid, out["metrics"]["stage"], "approved", "承認")
        else:
            pytest.fail("クリーンアップで止まらない")
    assert out["metrics"]["stage"] == "クリーンアップ" and out["cause"]["kind"] == "precondition"
    out = _until_release_gate(st, tid, repo, _env(repo, set()))
    assert _migrations(st, tid) == 1 and out["metrics"]["stage"] == "デプロイ"


def test_a_finished_cleanup_or_a_started_deploy_is_not_migrated(repo, st, capsys, monkeypatch):
    tid = _new_old(capsys, monkeypatch)
    with monkeypatch.context() as m:
        _no_migration(m)
        _until_release_gate(st, tid, repo, _env(repo, set()))
    assert run.migrate_definition(st, tid) is None and _migrations(st, tid) == 0
    ids = _ids(st, tid)
    assert ids.index("cleanup") < ids.index("deploy")


def test_a_migration_event_with_other_stages_is_ignored():
    snap = _old_snapshot()
    draft = run.model.EventDraft(run_view.DEFINITION_MIGRATED, run.ACTOR, None, payload={"order": ["知らない工程"]}, rev=1)
    bad = run.model.Event(1, datetime.now(timezone.utc), draft)
    assert run_view.migrated_order(snap, [bad]) == snap
