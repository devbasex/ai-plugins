"""NDF のテストに共通する前提（#1142・#1953 の決定 9）。プラグインの根に置き、リポジトリでもプラグインのキャッシュでも効く。

1. テストごとに、使用量の帳簿（`NDF_USAGE_DIR`）・プランの状態の実体（`NDF_SV_STATE_DIR`）・耐久の記録
   （`NDF_DBOS_DIR`）を別の一時ディレクトリへ向ける。帳簿を読むテストが、ほかのテストの書いた行を読まない。
   どれも書く時に作るため、ここではパスを決めるだけでディレクトリを作らない
2. テストの終わりに、本物の帳簿（`NDF_USAGE_DIR` を外したときに決まる置き場所）へそのテストの印（`pytest`）の
   付いた行が増えていたら、そのテストを落とす（I9）。増えたファイルと大きさを失敗の文に出す。印の無い行
   （利用者の会話のサブエージェントの hook が書く行など）では落とさない

リポジトリの根の `conftest.py` はプラグインのキャッシュに配られないため、この前提はここに置く
（2026-10-11 にキャッシュの中で走ったテストが本物の帳簿へ書いた）。
"""

from __future__ import annotations

import os
import sys
import tempfile
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts" / "lib"))
import usage_ledger  # noqa: E402

REAL_LEDGER = usage_ledger.ledger_dir({k: v for k, v in os.environ.items() if k != "NDF_USAGE_DIR"})


@pytest.fixture(scope="session")
def _ndf_isolated_base() -> object:
    with tempfile.TemporaryDirectory(prefix="ndf-tests-usage-") as tmp:
        yield Path(tmp)


@pytest.fixture(autouse=True)
def _isolated_usage_dirs(_ndf_isolated_base: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> object:
    """テストごとの置き場所を決め、終わりに本物の帳簿へ書いていないかを見る。"""
    base = Path(_ndf_isolated_base) / f"t{zlib.crc32(request.node.nodeid.encode()):08x}-{id(request):x}"
    monkeypatch.setenv("NDF_USAGE_DIR", str(base / "usage"))
    monkeypatch.setenv("NDF_SV_STATE_DIR", str(base / "sv"))
    monkeypatch.setenv("NDF_DBOS_DIR", str(base / "dbos"))
    before = usage_ledger.ledger_snapshot(REAL_LEDGER)
    yield
    leaked = usage_ledger.marked_rows(REAL_LEDGER, before, request.node.nodeid)
    if leaked:
        after = usage_ledger.ledger_snapshot(REAL_LEDGER)
        grown = {k: (before.get(k, 0), v) for k, v in after.items() if before.get(k, 0) != v}
        pytest.fail(f"テストが本物の使用量の帳簿（{REAL_LEDGER}）へ書いた: {leaked}（増えたファイルと大きさ: {grown}）", pytrace=False)
