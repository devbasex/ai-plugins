"""未知ランタイムを副作用なしで拒否する入口の現状固定。"""
import os
import pathlib
import subprocess

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts/launch-reviewer.sh"


def test_unknown_runtime_exits_before_writing_files(tmp_path):
    result = subprocess.run(
        ["bash", str(SCRIPT), "bogus", "1", "1"],
        env={**os.environ, "CROSS_REVIEW_TMP_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 1
    assert "未知のランタイム" in result.stderr
    assert list(tmp_path.iterdir()) == []
