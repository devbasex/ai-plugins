"""進行表の DB の負荷の測定（#3369 の受け入れ条件 P1-3・P1-6。#3344 の決定 82）。pytest は集めない。

34 本のプロセスが、それぞれ自分のスプリントの進行表へ、`ndf progress` と同じ手順（`open_store` →
1 つのトランザクションで事象を追記 → 閉じる）で事象を書き続ける。測るのは次の値で、結果を 1 行の JSON で出す。

- `database is locked`（止まった理由 store_busy）の回数
- 書き込みの待ち（トランザクションの開始から確定まで）の 95 パーセンタイルと最大
- 進行表の DB の `quick_check` を打った回数と 1 回あたりの所要時間（目印と maint_lock が効けば、プロセスの数に比例しない）

打ち方（plugins/ndf-next で）:

    uv run --frozen --project . python tests/progress/load_measure.py --processes 34 --seconds 1800

`--check-every` を渡すと、その秒数ごとに目印を消して、24 時間の期限が切れた状態を作る（検査の排他を負荷の中で確かめる）。
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ndf.core import durable  # noqa: E402
from ndf.progress import backup, model, store  # noqa: E402


def _counting_check(log_path: Path):
    original = durable.check_file

    def check(path: Path) -> bool:
        started = time.perf_counter()
        ok = original(path)
        with log_path.open("a") as f:
            f.write(f"{time.perf_counter() - started}\n")
        return ok

    return check


def _setup(url: str, root: Path, n: int) -> list[str]:
    ids = []
    with store.open_store(url, root) as st, store.transaction(st) as conn:
        for i in range(n):
            drafts = [model.EventDraft("created", "conductor", "実装", rev=1)]
            ids.append(store.create_table(conn, ("sprint", "load", f"s{i}"), None, {"stages": []}, drafts))
    return ids


def _one_write(url: str, root: Path, table_id: str, toggle: bool) -> float:
    kind = "disabled" if toggle else "enabled"
    with store.open_store(url, root) as st:
        started = time.perf_counter()
        with store.transaction(st) as conn:
            last = store.read_events(conn, table_id)[-1].seq
            store.append_events(conn, table_id, [model.EventDraft(kind, "conductor", "実装", reason="負荷", rev=1)], last)
        return time.perf_counter() - started


def _worker(url: str, root: str, table_id: str, deadline: float, interval: float, out: str) -> None:
    durable.check_file = _counting_check(Path(out).with_suffix(".checks"))
    waits, busy, other = [], 0, []
    toggle = True
    while time.time() < deadline:
        try:
            waits.append(_one_write(url, Path(root), table_id, toggle))
            toggle = not toggle
        except store.StoreBusy:
            busy += 1
        except Exception as exc:  # noqa: BLE001 測定: 想定の外の失敗も数えて結果に載せる
            other.append(repr(exc))
        time.sleep(random.uniform(0, 2 * interval))
    Path(out).write_text(json.dumps({"waits": waits, "busy": busy, "other": other[:5], "other_count": len(other)}))


def _expire_marks(root: Path, deadline: float, every: float) -> None:
    while time.time() < deadline:
        time.sleep(every)
        (root / store.CHECKED_MARK).unlink(missing_ok=True)


def _collect_report(work: Path, root: Path, options: argparse.Namespace) -> dict:
    """子プロセスが書いた結果（`w<i>.json`・`w<i>.checks`）を読み、1 行の JSON に出す値を組み立てる。"""
    rows = [json.loads((work / f"w{i}.json").read_text()) for i in range(options.processes)]
    waits = sorted(w for r in rows for w in r["waits"])
    logs = [work / f"w{i}.checks" for i in range(options.processes)]
    checks = [float(x) for log in logs if log.exists() for x in log.read_text().split()]
    return {
        "at": datetime.now(UTC).isoformat(),
        "processes": options.processes,
        "seconds": options.seconds,
        "interval": options.interval,
        "check_every": options.check_every,
        "writes": len(waits),
        "database_is_locked": sum(r["busy"] for r in rows),
        "other_errors": sum(r["other_count"] for r in rows),
        "other_samples": [s for r in rows for s in r["other"]][:5],
        "wait_p95_seconds": waits[int(0.95 * (len(waits) - 1))] if waits else None,
        "wait_max_seconds": waits[-1] if waits else None,
        "quick_check_count": len(checks),
        "quick_check_seconds_max": max(checks) if checks else None,
        "backups": len(list((root / backup.BACKUP_DIR).glob("*.sqlite"))),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--processes", type=int, default=34)
    ap.add_argument("--seconds", type=float, default=1800)
    ap.add_argument("--interval", type=float, default=1.0, help="1 プロセスの書き込みの間隔の平均（秒）")
    ap.add_argument("--check-every", type=float, default=0, help="目印を消す間隔（秒）。0 なら消さない")
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="ndf-progress-load-"))
    root = work / "state"
    root.mkdir()
    url = store.default_url(root)
    ids = _setup(url, root, a.processes)
    (root / store.CHECKED_MARK).unlink(missing_ok=True)
    deadline = time.time() + a.seconds
    procs = [
        mp.Process(target=_worker, args=(url, str(root), tid, deadline, a.interval, str(work / f"w{i}.json"))) for i, tid in enumerate(ids)
    ]
    if a.check_every:
        procs.append(mp.Process(target=_expire_marks, args=(root, deadline, a.check_every)))
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    report = _collect_report(work, root, a)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
