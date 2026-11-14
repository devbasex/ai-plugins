"""本物の状態の置き場への書き込みを拒む部品（#2776）。pytest に依らない。

テストの前提（pytest の fixture `forbid_real_state`）はテストの側（`tests/testing/_real_state.py`）が持ち、ここの部品を
組み合わせる: 状態の置き場を指す環境変数（`REDIRECTED_ENV`）を一時ディレクトリへ向け、`REMOVED_ENV` を外し、
`guarding` の間は本物の置き場（`protected_roots`。実行した人のホームの下など）を開いて書くと `RealStateWriteError` を送る。

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
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from ndf import identity
from ndf.testing import slots
from ndf.testing.testenv import within

# 一時ディレクトリへ向ける環境変数（値は一時ディレクトリの下の名前）
REDIRECTED_ENV = {
    identity.XDG_STATE_ENV: "state",
    "XDG_CACHE_HOME": "cache",
    "XDG_CONFIG_HOME": "config",
    "XDG_DATA_HOME": "data",
    slots.SLOTS_DIR_ENV: "test-slots",
}
# テストの間は外す環境変数（ほかのプラグインの根・状態の置き場の根の指定・実行中の工程の識別子）
REMOVED_ENV = ("CLAUDE_PLUGIN_ROOT", identity.STATE_HOME_ENV, slots.HELD_ENV)
# ホームの下の本物の置き場
REAL_STATE_UNDER_HOME = (".claude", ".local/state", ".config", ".local/share")

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
# 名前そのものを作る・消す・移す操作（リンクを辿らず、リンクの置かれたパスで判定し、守る根を下に含む対象も拒む）と、
# 中身を書き換える操作（リンクの参照先まで辿って判定する）
NAME = "name"
CONTENT = "content"
# 書き込みの監査イベントと、引数のうち（パスの位置, そのパスを解決する dir_fd の位置, 判定の種類）の組
_WRITE_EVENTS: dict[str, tuple[tuple[int, int | None, str], ...]] = {
    "os.mkdir": ((0, 2, NAME),),
    "os.remove": ((0, 1, NAME),),
    "os.rmdir": ((0, 1, NAME),),
    "os.rename": ((0, 2, NAME), (1, 3, NAME)),
    # 本物の置き場のファイルへのハードリンクは、別名への書き込みで本物を書き換えられるのでリンク元も見る
    "os.link": ((0, 2, CONTENT), (1, 3, NAME)),
    # リンクの作成先だけを見る（参照先への書き込みは、書くときに参照先で判定する）
    "os.symlink": ((1, 2, NAME),),
    "os.truncate": ((0, None, CONTENT),),
    "os.chmod": ((0, 2, CONTENT),),
    "shutil.rmtree": ((0, 1, NAME),),
}
# 元の値が指す場所も本物の置き場として守る環境変数（差し替える前の値を読む）
REAL_STATE_ENV = (identity.STATE_HOME_ENV, identity.XDG_STATE_ENV,"XDG_CONFIG_HOME", "XDG_DATA_HOME", slots.SLOTS_DIR_ENV)
# 読み込んだ時点（conftest の読み込み・fixture より前）の値。fixture の実行時には、先に動いたほかの autouse の
# fixture（例: XDG_STATE_HOME を tmp_path へ向けるもの）が書き換えていて、本来の置き場を読めないため
_ORIGINAL_ENV = {name: value for name in REAL_STATE_ENV if (value := os.environ.get(name)) is not None}
# 同じ理由で、ホームも読み込んだ時点の値を持つ（先に動いた fixture が HOME を一時領域へ向けても、本来のホームの下を守る）
_ORIGINAL_HOME = Path.home()


class RealStateWriteError(PermissionError):
    """テストが本物の状態の置き場へ書こうとした。"""


def _fd_path(fd: object) -> str | None:
    """記述子が指す場所の絶対パス。記述子でないか、読めないか、パイプなどパスを持たなければ None。"""
    if not isinstance(fd, int) or isinstance(fd, bool) or fd < 0:
        return None
    try:
        found = os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        get_path = getattr(fcntl, "F_GETPATH", None) if fcntl is not None else None
        if get_path is None:
            return None
        try:
            found = os.fsdecode(fcntl.fcntl(fd, get_path, bytes(1024)).rstrip(b"\0"))
        except OSError:  # noqa: NDF401 ファイル: 記述子の指す場所を読めなければパスを持たないとして扱う
            return None
    return found if os.path.isabs(found) else None


def _resolve(path: object, dir_fd: object, kind: str) -> str | None:
    """判定に使う絶対パス。`CONTENT` は参照先まで、`NAME` は親だけを解決してリンクの置かれたパスを返す。"""
    if isinstance(path, int) and not isinstance(path, bool):
        # 記述子は開いた先そのもの（中身を書き換える操作だけが記述子を受ける）
        return _fd_path(path)
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    if not isinstance(path, (str, os.PathLike)):
        return None
    text = os.fspath(path)
    if isinstance(text, bytes):
        text = os.fsdecode(text)
    base = _fd_path(dir_fd) if not os.path.isabs(text) else None
    # `..` を字面で畳まない（normpath / abspath を使わない）。`link/..` はリンクの参照先の親を指すので、
    # realpath に元の成分の順でリンクを解決させてから `..` を当てる
    full = os.path.join(base or os.getcwd(), text)
    name = os.path.basename(full)
    if kind == CONTENT or name in ("", ".", ".."):
        return os.path.realpath(full)
    return os.path.join(os.path.realpath(os.path.dirname(full)), name)


def _within_any(path: str, roots: tuple[str, ...]) -> bool:
    return any(within(path, root) for root in roots)


def _write_targets(event: str, args: tuple) -> list[tuple[object, object, str]]:
    """監査イベントが書き込む対象の（パス, そのパスを解決する dir_fd, 判定の種類）の並び。書き込みでなければ空。"""
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & _WRITE_FLAGS)
        return [(path, None, CONTENT)] if writing else []
    targets: list[tuple[object, object, str]] = []
    for at, fd_at, kind in _WRITE_EVENTS.get(event, ()):
        if at >= len(args):
            continue
        targets.append((args[at], args[fd_at] if fd_at is not None and fd_at < len(args) else None, kind))
    return targets


class _Guard:
    """監査フックは外せないので 1 度だけ入れ、守る根の一覧を差し替えて有効・無効を切り替える。

    中身を書き換える先は参照先まで、名前を作る・消す・移す先はリンクの置かれたパスまで解決し、参照先まで解決した
    守る根と比べる。名前を消す・移す先が守る根を下に含むとき（`~/.local` の移動など）も拒む。テストの一時ディレクトリ
    （`allowed`）の下だけは、守る根の下にあっても書ける。
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
        for path, dir_fd, kind in _write_targets(event, args):
            self._check(path, dir_fd, kind)

    def _check(self, path: object, dir_fd: object = None, kind: str = CONTENT) -> None:
        self.local.busy = True
        try:
            resolved = _resolve(path, dir_fd, kind)
        finally:
            self.local.busy = False
        if resolved is None or _within_any(resolved, self.allowed):
            return
        holds_root = kind == NAME and any(within(root, resolved) for root in self.roots)
        if not holds_root and not _within_any(resolved, self.roots):
            return
        self.violations.append(resolved)
        raise RealStateWriteError(f"テストが本物の状態の置き場へ書こうとした: {resolved}")


_guard = _Guard()


def real_roots(home: Path, environ: Mapping[str, str]) -> tuple[str, ...]:
    """守る根。ホームの下の置き場と、差し替える前の環境変数（`REAL_STATE_ENV`）が指す置き場（絶対パスだけ）。"""
    under_home = [os.path.realpath(home / sub) for sub in REAL_STATE_UNDER_HOME]
    from_env = [os.path.realpath(value) for name in REAL_STATE_ENV if (value := environ.get(name)) is not None and os.path.isabs(value)]
    return tuple(dict.fromkeys(under_home + from_env))


def protected_roots() -> tuple[str, ...]:
    """守る根。読み込んだ時点の値と今の値の両方が指す場所（今の値が一時領域なら、`guarding` の allowed が書き込みを通す）。

    差し替えの前に呼ぶ。"""
    return tuple(dict.fromkeys(real_roots(_ORIGINAL_HOME, _ORIGINAL_ENV) + real_roots(Path.home(), os.environ)))


@contextmanager
def guarding(roots: tuple[str, ...], allowed: tuple[str, ...]) -> Iterator[list[str]]:
    """間の書き込みのうち、`roots` の下（`allowed` の下は除く）への書き込みを `RealStateWriteError` で拒む。

    渡す一覧は、抜けたときに拒んだ書き込みのパスで埋まる（テストが例外を握りつぶしても、呼び手が落とせる）。
    `allowed` が守る根の下にあっても、書けるのはその下だけで、守る根は残る。
    """
    found: list[str] = []
    _guard.install()
    _guard.roots = roots
    _guard.allowed = tuple(os.path.realpath(path) for path in allowed)
    _guard.violations = []
    try:
        yield found
    finally:
        found += _guard.violations
        _guard.roots, _guard.allowed, _guard.violations = (), (), []
