"""本物の状態の置き場からテストを隔離する pytest の前提（#2776）。

`forbid_real_state` は、テストの間、状態の置き場を指す環境変数をすべてテストの一時ディレクトリへ向け、
本物の置き場（実行した人のホームの下）を開いて書くと落とす。使うには conftest で autouse の fixture として受ける:

    from ndf.testing.isolation import forbid_real_state  # noqa: F401

    @pytest.fixture(autouse=True)
    def _isolated(forbid_real_state): ...

監視は Python の監査フック（`sys.addaudithook`）で、このプロセスの中の書き込みだけを見る。子プロセスは環境変数の
差し替えで一時ディレクトリへ向く。
"""

from __future__ import annotations

import os
import sys
import threading

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
from collections.abc import Iterator
from pathlib import Path

import pytest

from ndf.testing import slots

# 一時ディレクトリへ向ける環境変数（値は一時ディレクトリの下の名前）
REDIRECTED_ENV = {
    "XDG_STATE_HOME": "state",
    "XDG_CACHE_HOME": "cache",
    "XDG_CONFIG_HOME": "config",
    "XDG_DATA_HOME": "data",
    slots.SLOTS_DIR_ENV: "test-slots",
}
# テストの間は外す環境変数（ほかのプラグインの根・実行中の工程の識別子）
REMOVED_ENV = ("CLAUDE_PLUGIN_ROOT", slots.HELD_ENV)
# ホームの下の本物の置き場
REAL_STATE_UNDER_HOME = (".claude", ".local/state", ".config", ".local/share")

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
# 書き込みの監査イベントと、引数のうち（パスの位置, そのパスを解決する dir_fd の位置）の組
_WRITE_EVENTS: dict[str, tuple[tuple[int, int | None], ...]] = {
    "os.mkdir": ((0, 2),),
    "os.remove": ((0, 1),),
    "os.rmdir": ((0, 1),),
    "os.rename": ((0, 2), (1, 3)),
    "os.truncate": ((0, None),),
    "os.chmod": ((0, 2),),
    "shutil.rmtree": ((0, 1),),
}


class RealStateWriteError(PermissionError):
    """テストが本物の状態の置き場へ書こうとした。"""


def _fd_dir(fd: object) -> str | None:
    """ディレクトリの記述子が指す場所。読めなければ None。"""
    if not isinstance(fd, int) or isinstance(fd, bool) or fd < 0:
        return None
    try:
        return os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        pass
    get_path = getattr(fcntl, "F_GETPATH", None) if fcntl is not None else None
    if get_path is None:
        return None
    try:
        return os.fsdecode(fcntl.fcntl(fd, get_path, bytes(1024)).rstrip(b"\0"))
    except OSError:
        return None


def _within(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == root or path.startswith(root + os.sep) for root in roots)


class _Guard:
    """監査フックは外せないので 1 度だけ入れ、守る根の一覧を差し替えて有効・無効を切り替える。

    書き込み先と守る根は、どちらもシンボリックリンクの参照先まで解決して比べる。テストの一時ディレクトリ
    （pytest の一時領域。`allowed`）の下だけは、守る根の下にあっても書ける。
    """

    def __init__(self) -> None:
        self.roots: tuple[str, ...] = ()
        self.allowed: tuple[str, ...] = ()
        self.violations: list[str] = []
        self.installed = False
        self.local = threading.local()

    def install(self) -> None:
        if not self.installed:
            sys.addaudithook(self._hook)
            self.installed = True

    def _hook(self, event: str, args: tuple) -> None:
        if not self.roots or getattr(self.local, "busy", False):
            return
        targets: list[tuple[object, object]] = []
        if event == "open":
            path, mode, flags = (tuple(args) + (None, None, None))[:3]
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & _WRITE_FLAGS)
            if writing:
                targets.append((path, None))
        elif event in _WRITE_EVENTS:
            for at, fd_at in _WRITE_EVENTS[event]:
                if at < len(args):
                    targets.append((args[at], args[fd_at] if fd_at is not None and fd_at < len(args) else None))
        for path, dir_fd in targets:
            self._check(path, dir_fd)

    def _check(self, path: object, dir_fd: object = None) -> None:
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        if not isinstance(path, (str, os.PathLike)):
            return
        self.local.busy = True
        try:
            text = os.fspath(path)
            base = _fd_dir(dir_fd) if not os.path.isabs(text) else None
            resolved = os.path.realpath(os.path.join(base, text) if base else text)
        finally:
            self.local.busy = False
        if _within(resolved, self.allowed) or not _within(resolved, self.roots):
            return
        self.violations.append(resolved)
        raise RealStateWriteError(f"テストが本物の状態の置き場へ書こうとした: {resolved}")


_guard = _Guard()


def real_roots(home: Path) -> tuple[str, ...]:
    return tuple(os.path.realpath(home / sub) for sub in REAL_STATE_UNDER_HOME)


@pytest.fixture
def forbid_real_state(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """状態の置き場を一時ディレクトリへ向け、本物の置き場への書き込みを落とす。一時ディレクトリの根を返す。"""
    roots = real_roots(Path.home())
    base = tmp_path_factory.mktemp("ndf-state")
    for name, sub in REDIRECTED_ENV.items():
        monkeypatch.setenv(name, str(base / sub))
    for name in REMOVED_ENV:
        monkeypatch.delenv(name, raising=False)
    # テストの一時領域が本物の置き場の下にあっても、書けるのはその一時領域だけで、守る根は残す
    _guard.install()
    _guard.roots = roots
    _guard.allowed = (os.path.realpath(tmp_path_factory.getbasetemp()),)
    _guard.violations = []
    try:
        yield base
    finally:
        found, _guard.roots, _guard.allowed, _guard.violations = list(_guard.violations), (), (), []
    if found:
        pytest.fail("テストが本物の状態の置き場へ書こうとした: " + ", ".join(sorted(set(found))), pytrace=False)
