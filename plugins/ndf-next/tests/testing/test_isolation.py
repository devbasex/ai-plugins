from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from ndf.testing import isolation

PLUGIN_ROOT = Path(__file__).resolve().parents[2]

INNER = textwrap.dedent(
    """
    import os
    from pathlib import Path
    import pytest

    @pytest.fixture(autouse=True)
    def _iso(forbid_real_state):
        return forbid_real_state

    def test_writes_to_the_real_state():
        target = Path.home() / ".local" / "state" / "ndf" / "ledger.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{壊れた行")

    def test_swallowed_write_still_fails():
        try:
            open(Path.home() / ".claude" / "x", "w")
        except OSError:
            pass

    def test_redirected_state_is_writable():
        Path(os.environ["XDG_STATE_HOME"]).mkdir(parents=True)
        (Path(os.environ["XDG_STATE_HOME"]) / "ok").write_text("ok")
    """
)


def test_real_state_writes_fail_and_redirected_ones_pass(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "test_inner.py").write_text(INNER)
    env = {**os.environ, "HOME": str(home), "PYTHONPATH": str(PLUGIN_ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "ndf.testing.isolation", "-q", "-rfE", "-p", "no:cacheprovider", "test_inner.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    flagged = {line.split("::")[1].split()[0] for line in proc.stdout.splitlines() if line.startswith(("FAILED", "ERROR"))}
    assert flagged == {"test_writes_to_the_real_state", "test_swallowed_write_still_fails"}, proc.stdout
    assert not (home / ".local" / "state" / "ndf").exists()
    assert not (home / ".claude" / "x").exists()


def test_state_variables_point_at_the_temporary_directory(forbid_real_state):
    for name, sub in isolation.REDIRECTED_ENV.items():
        assert os.environ[name] == str(forbid_real_state / sub)
    assert "CLAUDE_PLUGIN_ROOT" not in os.environ
