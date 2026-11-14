"""I5: 同じ SQLite のファイルを別の鍵で開いたプロセスは、相手のキューの実行を取り出さない（決定 81）。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ndf.core import durable

SRC = Path(__file__).resolve().parents[2] / "src"
QUEUE_SCRIPT = Path(__file__).with_name("_durable_queue.py")


def _run(url: str, key: str, mode: str, log: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run(
        [sys.executable, str(QUEUE_SCRIPT), url, key, mode, str(log)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


def test_a_process_does_not_dequeue_another_keys_queue(tmp_path):
    url = f"sqlite:///{tmp_path / 'shared.sqlite'}"
    log = tmp_path / "ran.log"

    first = _run(url, "lane-a", "delayed", log)
    assert first.returncode == 0, first.stderr
    second = _run(url, "lane-b", "run", log)
    assert second.returncode == 0, second.stderr

    # lane-b は自分の実行だけを流し、lane-a の実行は取り出せる時刻を過ぎても残る
    assert log.read_text().splitlines() == ["lane-b by lane-b"]
    records = {r.workflow_id: r for r in durable.workflows(url)}
    assert records["wf-lane-b"].status == "SUCCESS"
    assert records["wf-lane-b"].run_name == durable.run_name("lane-b")
    assert records["wf-lane-a"].status in {"DELAYED", "ENQUEUED"}
    assert records["wf-lane-a"].run_name == durable.run_name("lane-a")
