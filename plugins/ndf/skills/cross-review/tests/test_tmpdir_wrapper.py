"""cross-review 固有の tmpdir wrapper の現状固定テスト。"""
from __future__ import annotations

import os
import pathlib
import subprocess


_TMPDIR = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "_tmpdir.sh"


def _run_tmpdir(cwd: pathlib.Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    script = f'. "{_TMPDIR}"; tmpdir'
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        cwd=cwd,
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_tmpdir_prefers_cross_review_tmp_dir(tmp_path):
    explicit = tmp_path / "chosen"

    proc = _run_tmpdir(tmp_path, {"CROSS_REVIEW_TMP_DIR": str(explicit)})

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(explicit)
    assert explicit.is_dir()


def test_tmpdir_defaults_to_cross_review_under_the_worktree(tmp_path, monkeypatch):
    monkeypatch.delenv("CROSS_REVIEW_TMP_DIR", raising=False)

    proc = _run_tmpdir(tmp_path)

    expected = tmp_path / ".cross_review"
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(expected)
    assert expected.is_dir()
