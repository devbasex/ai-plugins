"""導入の確認の出力を走っている間にログへ書き、伸びたことだけを標準エラーへ知らせる（#2567）。"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / "lib")]
from release_lib import deploy  # noqa: E402
from step_result import StepError  # noqa: E402


@pytest.fixture()
def here(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(deploy, "head_of_base", lambda root, base: "abc")
    monkeypatch.setattr(deploy, "WATCH_SECONDS", 0.05)
    monkeypatch.setattr(deploy, "REPORT_SECONDS", 0.1)
    return tmp_path


def test_the_output_is_kept_private_and_only_its_size_is_reported(here: Path, capfd: pytest.CaptureFixture) -> None:
    out = here / "out" / "facts.md"
    secret = "s3cret-value"

    got = deploy.run_verify(here, "main", f"echo {secret}; echo err >&2; sleep 0.4; exit 3", str(out))

    log = Path(got["log"])
    assert got["verify_exit"] == 3 and log == Path(f"{out}.verify.log")
    assert log.read_text(encoding="utf-8").splitlines() == [secret, "err"]
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    captured = capfd.readouterr()
    assert "導入の確認の出力: " in captured.err and "バイト（" in captured.err
    assert secret not in captured.err and secret not in captured.out


def test_no_growth_reports_nothing(here: Path, capfd: pytest.CaptureFixture) -> None:
    deploy.run_verify(here, "main", "sleep 0.3", str(here / "facts.md"))

    assert capfd.readouterr().err == ""


def test_what_was_written_survives_a_kill(tmp_path: Path) -> None:
    """遅れの見張りがプロセスグループごと止めても、それまでの出力が残る。"""
    out = tmp_path / "facts.md"
    code = (
        "import sys; sys.path[:0] = [sys.argv[1], sys.argv[1] + '/lib'];"
        "from release_lib import deploy;"
        "deploy.head_of_base = lambda root, base: 'abc';"
        "deploy.run_verify(sys.argv[2], 'main', 'echo before; sleep 30; echo after', sys.argv[3])"
    )
    p = subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), str(tmp_path), str(out)], start_new_session=True)
    log = Path(f"{out}.verify.log")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (log.exists() and log.stat().st_size):
        time.sleep(0.05)

    os.killpg(p.pid, 9)
    p.wait()

    assert log.read_text(encoding="utf-8") == "before\n"


def test_an_unwritable_log_stops_before_running(here: Path) -> None:
    marker = here / "ran"
    (here / "file").write_text("x", encoding="utf-8")

    with pytest.raises(StepError):
        deploy.run_verify(here, "main", f"touch {marker}", str(here / "file" / "facts.md"))

    assert not marker.exists()
