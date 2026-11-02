"""テストが起こした子孫のプロセスを、pytest が止められたとき・終わったときに残さない（#2291 の決定 4）。

根の `conftest.py` が `pytest_plugins` で読み込む。pytest の親と xdist の各 worker で、次の 3 つを行う。

1. 子の回収役（Linux の `PR_SET_CHILD_SUBREAPER`）になる。テストが自分の session で起こした子の親が先に
   終わっても、孫は init ではなく最も近い pytest のプロセスへ付け替わり、止める対象から漏れない
2. SIGCHLD を受けるたびに、自分が起こした覚えの無い直接の子のゾンビだけを回収する。付け替わった孫の
   ゾンビが残ると、打ち切る側のグループの存否の判定が成功し続け、猶予を毎回待ち切る。
   自分が起こした pid は `subprocess.Popen` と `os.fork` を包んで記録し、その終了コードは横取りしない。
   起動から記録までの間（起動中の数が 1 以上の間）は回収を見送り、数が 0 に戻った時点でやり直す
3. SIGTERM を受けたとき・pytest が終わるとき（`pytest_unconfigure`）に、直接の子の木を止める。
   止めるのは `lib/procs.py` の `stop_tree` で、自分の pid には向けない

子の回収役にできない環境（Linux 以外・psutil が無い）では何もしない。テストの結果は変えない。
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

PR_SET_CHILD_SUBREAPER = 36
GRACE = 2.0

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "ndf" / "scripts" / "lib"))
try:
    import procs
except ImportError:  # psutil が無い環境では何もしない
    procs = None

_lock = threading.Lock()
_own: set[int] = set()
_state = {"starting": 0, "deferred": False, "config": None}
_saved: dict = {}


def _become_subreaper() -> bool:
    if procs is None or not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False


def _reap_unknown() -> None:
    """直接の子のうち、ゾンビで、自分が起こした記録に無いものだけを 1 つずつ回収する（ロックを持って呼ぶ）。"""
    for pid in procs.child_pids(os.getpid(), recursive=False):
        if pid in _own or not procs.pid_is_zombie(pid):
            continue
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass


def _reap_while_deferred() -> None:
    """見送った回収を、起動中の数が 0 の間にやり直す。"""
    while _state["deferred"]:
        with _lock:
            if _state["starting"]:
                return
            _state["deferred"] = False
            _reap_unknown()


def _begin() -> None:
    with _lock:
        _state["starting"] += 1


def _end(pid: int | None) -> None:
    with _lock:
        if pid:
            _own.add(pid)
        _state["starting"] -= 1
    _reap_while_deferred()


def _reset_in_forked_child() -> None:
    global _lock
    _lock = threading.Lock()
    _own.clear()
    _state["starting"] = 0
    _state["deferred"] = False


def _on_sigchld(signum, frame) -> None:
    # 主スレッドで走り、主スレッドの包んだ呼び出しの途中にも割り込むため、ロックを待たない
    if not _lock.acquire(blocking=False):
        _state["deferred"] = True
        return
    try:
        if _state["starting"]:
            _state["deferred"] = True
        else:
            _state["deferred"] = False
            _reap_unknown()
    finally:
        _lock.release()


def stop_children() -> None:
    """直接の子の各 pid へ `stop_tree` を並べてかけ、全部の終わりをまとめて待つ。自分の pid には送らない。"""
    me = os.getpid()
    pids = [p for p in procs.child_pids(me, recursive=False) if p != me]
    threads = [threading.Thread(target=procs.stop_tree, args=(p, GRACE), daemon=True) for p in pids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def _on_sigterm(signum, frame) -> None:
    stop_children()
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGTERM)


def _wrap_popen_init(orig):
    def __init__(self, *args, **kwargs):
        _begin()
        pid = None
        try:
            orig(self, *args, **kwargs)
            pid = getattr(self, "pid", None)
        finally:
            _end(pid)

    return __init__


def _wrap_fork(orig):
    def fork():
        _begin()
        pid = None
        try:
            pid = orig()
        finally:
            if pid == 0:
                _reset_in_forked_child()
            else:
                _end(pid)
        return pid

    return fork


def install() -> bool:
    """子の回収役になり、包みとシグナルの扱いを置く。置けたら真。"""
    if _saved or not _become_subreaper():
        return False
    try:
        _saved["sigchld"] = signal.signal(signal.SIGCHLD, _on_sigchld)
        signal.siginterrupt(signal.SIGCHLD, False)
        _saved["sigterm"] = signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:  # 主スレッドでないとシグナルの扱いを置けない
        if "sigchld" in _saved:
            signal.signal(signal.SIGCHLD, _saved.pop("sigchld"))
        return False
    _saved["popen_init"] = subprocess.Popen.__init__
    _saved["fork"] = os.fork
    subprocess.Popen.__init__ = _wrap_popen_init(_saved["popen_init"])
    os.fork = _wrap_fork(_saved["fork"])
    return True


def uninstall() -> None:
    """残った子孫を止め、記録に無いゾンビを回収し、包みとシグナルの扱いを元へ戻す。"""
    if not _saved:
        return
    stop_children()
    with _lock:
        _reap_unknown()
    subprocess.Popen.__init__ = _saved.pop("popen_init")
    os.fork = _saved.pop("fork")
    signal.signal(signal.SIGCHLD, _saved.pop("sigchld"))
    signal.signal(signal.SIGTERM, _saved.pop("sigterm"))


def pytest_configure(config) -> None:
    if _state["config"] is None and install():
        _state["config"] = config


def pytest_unconfigure(config) -> None:
    if _state["config"] is config:
        uninstall()
        _state["config"] = None
