"""承認ゲート（`ndf.progress.gate`）のテストが共有する部品。

ステートマシンは standard のモード（設計承認ゲートを持つ。#3425）で作り、偽の作業（定義した出力を持つ ok を返す）で設計承認ゲートまで流す。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from ndf.progress import command, gate, lane, plan, run, store
from plan_decls import REPO
from plan_decls import decl as _decl
from plan_decls import doc as _doc
from result_json import last_result

ISSUE = 3092


def _load_lanes():
    """レーンの部品（tests/progress/lanes.py。偽の作業の結果と 1 手の打ち方）。"""
    spec = importlib.util.spec_from_file_location("lanes", Path(__file__).parents[1] / "lanes.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lanes = _load_lanes()


@pytest.fixture
def env(repo):
    values = lanes.values(repo, "sprint/s1")
    return run.Env(
        facts=lambda words, ctx, cwd: {w: True for w in words},
        launch=lambda st_, tid, cwd: None,
        invoker=lambda r, cwd, work_dir, answer: lanes.ok_result(r, values),
        prepare=lambda r, work_dir, cwd: None,  # 作業が偽物なのでプランの差し込みも組まない
        records=lambda cwd: lanes._NoRecords(),
        sleep=lambda _s: None,
    )


@pytest.fixture
def new_table(capsys):
    """standard のステートマシンを作って ID を返す。`extra` は `ndf progress new` へ足す引数。"""

    def make(*extra: str) -> str:
        code = command.main(["new", "s1", "--mode", "standard", "--issues", str(ISSUE), *extra])
        out = last_result(capsys.readouterr().out)
        assert code == 0, out
        return out["metrics"]["machine_id"]

    return make


@pytest.fixture
def flow(repo, st, env):
    """`next` を打ち、起動した作業を同じプロセスで流す。`with_env` は既定の偽の作業の代わりに使う `Env`。"""
    return SimpleNamespace(turn=lambda tid, with_env=None: lanes.turn(st, tid, repo, with_env if with_env is not None else env), repo=repo)


@pytest.fixture
def release_question(st, flow):
    """設計承認ゲートを承認してリリース承認ゲートまで進め、その質問を返す関数。"""

    def make(tid: str):
        flow.turn(tid)
        q = gate.waiting(run.read(st, tid))
        gate.answer(st, tid, gate.Answer(q.id, "approved", "設計を承認", "human:reviewer", "k-design", seen=gate.Target(head="d1")))
        flow.turn(tid)
        q = gate.waiting(run.read(st, tid))
        assert q is not None and q.gate == "release"
        return q

    return make


@pytest.fixture
def lane_sprint(st, new_table):
    """計画のレーン a の 1 行目で動くスプリントのステートマシンを作り、（計画の ID, ステートマシンの ID）を返す。`extra` は `ndf progress new` へ足す引数。"""

    def make(*extra: str) -> tuple[str, str]:
        with store.transaction(st) as conn:
            pid = plan.create_plan(conn, REPO, _doc(_decl("a", [[3092], [2]])), True)
            view, _ = lane.read_plan(conn, pid)
        tid = new_table("--parent", view.lanes["a"].machine_id, *extra)
        with store.transaction(st) as conn:
            lane.start_row(conn, pid, "a", "r1", tid)
        return pid, tid

    return make
