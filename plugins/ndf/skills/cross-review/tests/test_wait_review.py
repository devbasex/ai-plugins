"""wait-review.sh wrapper の引数不足経路を固定する。"""
from __future__ import annotations

import pathlib
import shutil
import subprocess


_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "wait-review.sh"


def test_wait_review_fails_before_monitor_when_pr_is_missing(tmp_path):
    script = tmp_path / "wait-review.sh"
    monitor = tmp_path / "monitor.py"
    called = tmp_path / "monitor-called"
    shutil.copy2(_SCRIPT, script)
    monitor.write_text(
        "#!/usr/bin/env bash\n"
        f"touch {called}\n"
        "exit 99\n",
        encoding="utf-8",
    )
    monitor.chmod(0o755)

    proc = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode != 0
    assert "PR required" in proc.stderr
    assert not called.exists()
