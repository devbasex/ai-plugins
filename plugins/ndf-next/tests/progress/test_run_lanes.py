"""6 本のレーンが `ndf progress next` で進行表の DB へ同時に書いても、`database is locked` が 0 回である（#3092）。

全員そろえてから走らせ、決めた回数（レーンごとに `ROUNDS` 回のスプリント）を回す。時間で区切らず、所要時間の合否を
持たない。DBOS の記録は鍵ごとに分かれる（#3344 の決定 81）ため、見るのは進行表の DB だけである。
"""

from __future__ import annotations

import multiprocessing as mp
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from ndf import identity
from ndf.core import durable
from ndf.progress import store

LANES = 6
ROUNDS = 3
JOIN_SECONDS = 600


def test_six_lanes_write_the_progress_db_at_once_without_lock_errors(tmp_path, monkeypatch):
    repo = tmp_path / "work"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.chdir(repo)
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    import lanes

    root = durable.store_root(identity.load().name, repo)
    url = store.default_url(root)
    # 置き場と表は先に 1 回用意する（6 本の最初の new が置き場の形の番号を同時に書きにいかないように）
    durable.prepare_store(root, datetime.now(UTC))
    with store.open_store(url, root):
        pass
    ctx = mp.get_context("spawn")
    barrier, out = ctx.Barrier(LANES), ctx.Queue()
    procs = [ctx.Process(target=lanes.lane, args=(url, str(root), str(repo), f"lane{i}", ROUNDS, barrier, out)) for i in range(LANES)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(JOIN_SECONDS)
    try:
        assert [p.exitcode for p in procs] == [0] * LANES
        got = sorted(out.get(timeout=10) for _ in procs)
        assert [busy for _, busy, _ in got] == [0] * LANES
        assert [done for _, _, done in got] == [ROUNDS] * LANES
    finally:
        for p in procs:
            if p.is_alive():
                p.kill()
        durable.release_store(root)
