"""並列の計画のレーンのテスト（`test_parallel_lane.py`・`test_parallel_ask.py`）に共通する試験基盤: 1 レーンの計画を置く
`plan_main` と、`parallel.py` を読み込んで副命令を打つ `parallel_cli`・`run_cli`（テストとしては集めない）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import parallel_lane as pl  # noqa: E402
import parallel_plan as pp  # noqa: E402

PLAN = {
    "version": 1,
    "name": "p1",
    "rules": [],
    "lanes": [{"name": "la", "input": {"kind": "sprint", "issues": [1]}, "plans": 2, "touches": ["a/"]}],
}


def plan_main(tmp_path: Path) -> Path:
    """1 レーン（`la`）の計画 `p1` を `tmp_path` に置き、メインディレクトリとして返す。"""
    d = pp.plan_dir(tmp_path, "p1")
    d.mkdir(parents=True)
    (d / "plan.json").write_text(json.dumps(PLAN))
    return tmp_path


def parallel_cli(monkeypatch, tmp_path):
    """`parallel.py` を読み込み、メインディレクトリを `tmp_path`、計画を動いている状態にした (モジュール, parallel_ledger)。"""
    import importlib.util

    import parallel_ledger as plg

    spec = importlib.util.spec_from_file_location("parallel_cli_request", SCRIPTS / "parallel.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "main_of", lambda: tmp_path)
    monkeypatch.setattr(pl, "run_status", lambda main, name: next(iter(pl.RUNNING)))
    return mod, plg


def run_cli(mod, capsys, *argv):
    """副命令を打ち、(終了コード, 最後の行の JSON)。"""
    import pytest

    with pytest.raises(SystemExit) as e:
        mod.main(list(argv))
    return e.value.code or 0, json.loads(capsys.readouterr().out.strip().splitlines()[-1])
