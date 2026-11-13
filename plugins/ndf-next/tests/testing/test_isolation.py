from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

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

    def test_symlink_into_the_real_state_fails(tmp_path):
        (tmp_path / "link").symlink_to(Path.home() / ".local" / "state")
        (tmp_path / "link" / "ledger.jsonl").write_text("x")

    def test_dir_fd_removal_in_the_real_state_fails():
        real = Path.home() / ".claude"
        fd = os.open(real, os.O_RDONLY)
        try:
            os.remove("kept", dir_fd=fd)
        finally:
            os.close(fd)

    def test_hard_link_from_the_real_state_fails(tmp_path):
        alias = tmp_path / "alias"
        os.link(Path.home() / ".claude" / "kept", alias)
        alias.write_text("x")

    def test_redirected_state_is_writable():
        Path(os.environ["XDG_STATE_HOME"]).mkdir(parents=True)
        (Path(os.environ["XDG_STATE_HOME"]) / "ok").write_text("ok")
    """
)


FLAGGED = {
    "test_writes_to_the_real_state",
    "test_swallowed_write_still_fails",
    "test_symlink_into_the_real_state_fails",
    "test_dir_fd_removal_in_the_real_state_fails",
    "test_hard_link_from_the_real_state_fails",
}


@pytest.mark.parametrize("basetemp_under_real_state", [False, True])
def test_real_state_writes_fail_and_redirected_ones_pass(tmp_path, basetemp_under_real_state):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "kept").write_text("kept")
    (home / ".local" / "state").mkdir(parents=True)
    (tmp_path / "test_inner.py").write_text(INNER)
    env = {**os.environ, "HOME": str(home), "PYTHONPATH": str(PLUGIN_ROOT / "src")}
    # 一時ディレクトリを本物の置き場の下に置いても、その外の本物の置き場は守られたまま
    basetemp = ["--basetemp", str(home / ".local" / "state" / "pytest")] if basetemp_under_real_state else []
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "ndf.testing.isolation", "-q", "-rfE", "-p", "no:cacheprovider", *basetemp, "test_inner.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    flagged = {line.split("::")[1].split()[0] for line in proc.stdout.splitlines() if line.startswith(("FAILED", "ERROR"))}
    assert flagged == FLAGGED, proc.stdout
    assert not (home / ".local" / "state" / "ndf").exists()
    assert not (home / ".local" / "state" / "ledger.jsonl").exists()
    assert not (home / ".claude" / "x").exists()
    assert (home / ".claude" / "kept").read_text() == "kept"


def test_state_variables_point_at_the_temporary_directory(forbid_real_state):
    for name, sub in isolation.REDIRECTED_ENV.items():
        assert os.environ[name] == str(forbid_real_state / sub)
    assert "CLAUDE_PLUGIN_ROOT" not in os.environ
