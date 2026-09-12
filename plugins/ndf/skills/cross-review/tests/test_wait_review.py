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


def test_wait_review_passes_args_to_monitor(tmp_path):
    script = tmp_path / "wait-review.sh"
    monitor = tmp_path / "monitor.py"
    args_record = tmp_path / "args.txt"
    
    shutil.copy2(_SCRIPT, script)
    monitor.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"with open('{args_record}', 'w') as f:\n"
        "    f.write(repr(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    monitor.chmod(0o755)

    # PR だけ渡す
    subprocess.run(
        ["bash", str(script), "123"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert args_record.read_text() == "['123', 'both']"

    # 追加引数を渡す
    subprocess.run(
        ["bash", str(script), "123", "codex", "--timeout", "100", "--stall-timeout", "50"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert args_record.read_text() == "['123', 'codex', '--timeout', '100', '--stall-timeout', '50']"
