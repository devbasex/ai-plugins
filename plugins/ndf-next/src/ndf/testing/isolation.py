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
_WRITE_EVENTS = ("os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.truncate", "os.chmod", "shutil.rmtree")


class RealStateWriteError(PermissionError):
    """テストが本物の状態の置き場へ書こうとした。"""


class _Guard:
    """監査フックは外せないので 1 度だけ入れ、守る根の一覧を差し替えて有効・無効を切り替える。"""

    def __init__(self) -> None:
        self.roots: tuple[str, ...] = ()
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
        paths: tuple = ()
        if event == "open":
            path, mode, flags = (tuple(args) + (None, None, None))[:3]
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & _WRITE_FLAGS)
            paths = (path,) if writing else ()
        elif event in _WRITE_EVENTS:
            paths = tuple(a for a in args[:2])
        for path in paths:
            self._check(path)

    def _check(self, path: object) -> None:
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        if not isinstance(path, (str, os.PathLike)):
            return
        self.local.busy = True
        try:
            text = os.path.abspath(os.fspath(path))
        finally:
            self.local.busy = False
        if any(text == root or text.startswith(root + os.sep) for root in self.roots):
            self.violations.append(text)
            raise RealStateWriteError(f"テストが本物の状態の置き場へ書こうとした: {text}")


_guard = _Guard()


def real_roots(home: Path) -> tuple[str, ...]:
    return tuple(os.path.abspath(home / sub) for sub in REAL_STATE_UNDER_HOME)


@pytest.fixture
def forbid_real_state(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """状態の置き場を一時ディレクトリへ向け、本物の置き場への書き込みを落とす。一時ディレクトリの根を返す。"""
    roots = real_roots(Path.home())
    base = tmp_path_factory.mktemp("ndf-state")
    for name, sub in REDIRECTED_ENV.items():
        monkeypatch.setenv(name, str(base / sub))
    for name in REMOVED_ENV:
        monkeypatch.delenv(name, raising=False)
    # 一時ディレクトリが本物の置き場の下にあるときは、そこだけ書けるよう守る根から外す
    _guard.install()
    _guard.roots = tuple(r for r in roots if not str(base).startswith(r + os.sep))
    _guard.violations = []
    try:
        yield base
    finally:
        found, _guard.roots, _guard.violations = list(_guard.violations), (), []
    if found:
        pytest.fail("テストが本物の状態の置き場へ書こうとした: " + ", ".join(sorted(set(found))), pytrace=False)
