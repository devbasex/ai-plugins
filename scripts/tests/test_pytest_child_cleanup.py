"""テストが起こした子孫のプロセスを、pytest を止めたときに残さない（#2291 の受け入れ条件 6・7、決定 4）。

- 打ち切る側（`test_triage.stop_group`）がグループを止めても、自分の session の子と、親が先に終わった孫が残らない
- 親だけに SIGTERM を送っても、xdist の worker とその子孫が残らない
- 子の回収役にしても、`subprocess` と `os.fork` の呼び手が受け取る終了コードは変わらない
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "plugins" / "ndf" / "scripts" / "lib"))

import pytest_child_cleanup  # noqa: E402
import test_triage  # noqa: E402

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="子の回収役は Linux だけ")


def _bundle(tmp_path: Path) -> Path:
    """自分の session の子と、親が先に終わる孫を起こして待つテスト 2 件の束。"""
    b = tmp_path / "bundle"
    b.mkdir()
    (b / "pytest.ini").write_text("[pytest]\n")
    (b / "conftest.py").write_text(f"import sys\nsys.path.insert(0, {str(SCRIPTS)!r})\npytest_plugins = ['pytest_child_cleanup']\n")
    sleeper = f"{sys.executable} -c 'import time; time.sleep(60)' {b}"
    (b / "test_spawn.py").write_text(
        textwrap.dedent(f"""
        import subprocess, sys, time, pytest
        @pytest.mark.parametrize("n", [0, 1])
        def test_spawn(n):
            subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", {str(b)!r}], start_new_session=True)
            subprocess.Popen(["setsid", "sh", "-c", {sleeper + " &"!r}])
            open({str(b)!r} + f"/ready-{{n}}", "w").close()
            time.sleep(60)
        """)
    )
    return b


def _start(b: Path) -> subprocess.Popen:
    p = subprocess.Popen(
        [sys.executable, "-m", "pytest", str(b), "-q", "-n", "2", "-p", "no:cacheprovider"],
        cwd=b,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 60
    while not all((b / f"ready-{n}").exists() for n in (0, 1)):
        assert time.monotonic() < deadline, "束のテストが子を起こすところまで進まない"
        time.sleep(0.1)
    time.sleep(0.5)  # setsid の親が終わり、孫が付け替わるのを待つ
    return p


def _left(b: Path) -> list[int]:
    found = []
    for p in psutil.process_iter(["cmdline", "status"]):
        try:
            if p.info["status"] != psutil.STATUS_ZOMBIE and str(b) in " ".join(p.info["cmdline"] or []):
                found.append(p.pid)
        except psutil.Error:
            pass
    return found


def _assert_none_left(b: Path) -> None:
    deadline = time.monotonic() + 10
    while (left := _left(b)) and time.monotonic() < deadline:
        time.sleep(0.2)
    for pid in left:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    assert left == [], f"止めた後も残っている: {left}"


def test_stopping_the_group_leaves_no_test_children(tmp_path):
    b = _bundle(tmp_path)
    p = _start(b)
    test_triage.stop_group(p)
    _assert_none_left(b)


def test_sigterm_to_the_parent_alone_stops_workers_and_their_children(tmp_path):
    b = _bundle(tmp_path)
    p = _start(b)
    os.kill(p.pid, signal.SIGTERM)
    p.wait(timeout=10)
    _assert_none_left(b)


@pytest.mark.filterwarnings("ignore:This process:DeprecationWarning")
def test_exit_codes_reach_their_callers_under_the_reaper():
    assert pytest_child_cleanup._saved, "根の conftest が後始末の部品を置いていない"
    codes: list[int] = []

    def run():
        codes.extend(subprocess.run(["false"]).returncode for _ in range(200))

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    run()
    for t in threads:
        t.join()
    assert codes == [1] * 1000

    pid = os.fork()
    if pid == 0:
        os._exit(7)
    assert os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) == 7


def test_stopping_children_spares_itself_and_waits_for_them_together(tmp_path):
    script = textwrap.dedent(f"""
        import subprocess, sys, time
        sys.path.insert(0, {str(SCRIPTS)!r})
        import pytest_child_cleanup as c
        kids = [subprocess.Popen(["bash", "-c", "trap '' TERM; sleep 60"]) for _ in range(2)]
        time.sleep(0.3)
        start = time.monotonic()
        c.stop_children()
        print(round(time.monotonic() - start, 1), [k.poll() is not None for k in kids])
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    seconds, stopped = out.stdout.split(" ", 1)
    assert float(seconds) < 5
    assert stopped.strip() == "[True, True]"
