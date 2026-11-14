"""承認ゲート（`ndf.progress.gate`）のテストが共有する部品。

進行表は standard のモード（設計承認ゲートを持つ。#3425）で作り、偽の作業（宣言した出力を持つ ok を返す）で設計承認ゲートまで流す。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ndf.core import durable
from ndf.progress import command, run, store

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
def st(repo):
    root = durable.prepare_store(command.store_root_of(repo)).root
    with store.open_store(store.default_url(root), root) as opened:
        yield opened


@pytest.fixture
def env(repo):
    values = lanes.values(repo, "sprint/s1")
    return run.Env(
        facts=lambda words, ctx, cwd: {w: True for w in words},
        launch=lambda st_, tid, cwd: None,
        invoker=lambda r, cwd, work_dir, answer: lanes.ok_result(r, values),
        records=lambda cwd: lanes._NoRecords(),
        sleep=lambda _s: None,
    )


@pytest.fixture
def new_table(capsys):
    """standard の進行表を作って ID を返す。`extra` は `ndf progress new` へ足す引数。"""

    def make(*extra: str) -> str:
        code = command.main(["new", "s1", "--mode", "standard", "--issues", str(ISSUE), *extra])
        out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert code == 0, out
        return out["metrics"]["table_id"]

    return make


@pytest.fixture
def flow(repo, st, env):
    """`next` を打ち、起こした作業を同じプロセスで流す。`with_env` は既定の偽の作業の代わりに使う `Env`。"""
    return SimpleNamespace(turn=lambda tid, with_env=None: lanes.turn(st, tid, repo, with_env if with_env is not None else env))
