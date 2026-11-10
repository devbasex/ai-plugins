"""whole-test-timing.py の measure が各 suite の pytest へ読み込ませるプラグイン（#2309）。

集められた nodeid と、ほかの実装が項目を外した後に残った nodeid を、プロセスごとに 1 つの JSON へ書く。差が
deselect（集められて実行から外されたテスト）になる。measure の渡した suite の作業ディレクトリと `config.rootpath` が
一致するセッションだけを記録する。テストの中で起動する入れ子の pytest へ読み込ませないため、自分の環境変数を
`os.environ` から外す（xdist の controller は worker を起動した後、worker と xdist を使わない実行は configure で）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

ENV_DIR = "WTT_COLLECT_DIR"
ENV_ROOT = "WTT_COLLECT_ROOT"
ENV_PATH = "WTT_COLLECT_PYTHONPATH"
MODULE = "wtt_collect"

_state: dict = {}


def strip_env(env=os.environ) -> None:
    """PYTEST_PLUGINS の自分の項目・PYTHONPATH に足した置き場・記録の置き場を渡す変数を外す。"""
    plugins = [p for p in env.get("PYTEST_PLUGINS", "").split(",") if p.strip() and p.strip() != MODULE]
    if plugins:
        env["PYTEST_PLUGINS"] = ",".join(plugins)
    else:
        env.pop("PYTEST_PLUGINS", None)
    added = env.pop(ENV_PATH, None)
    if added is not None:
        rest = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p and p != added]
        if rest:
            env["PYTHONPATH"] = os.pathsep.join(rest)
        else:
            env.pop("PYTHONPATH", None)
    env.pop(ENV_DIR, None)
    env.pop(ENV_ROOT, None)


def _uses_xdist_controller(config) -> bool:
    return not hasattr(config, "workerinput") and getattr(config.option, "dist", "no") != "no"


def pytest_configure(config):
    out, root = os.environ.get(ENV_DIR), os.environ.get(ENV_ROOT)
    if out and root and Path(str(config.rootpath)).resolve() == Path(root).resolve():
        _state["out"] = Path(out)
    if not _uses_xdist_controller(config):
        strip_env()


@pytest.hookimpl(wrapper=True)
def pytest_sessionstart(session):
    try:
        return (yield)
    finally:
        strip_env()


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(session, config, items):
    collected = [i.nodeid for i in items]
    res = yield
    out = _state.get("out")
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        data = {"collected": collected, "kept": [i.nodeid for i in items]}
        (out / f"{os.getpid()}.json").write_text(json.dumps(data), encoding="utf-8")
    return res
