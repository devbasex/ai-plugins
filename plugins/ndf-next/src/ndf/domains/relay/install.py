"""PATH の入口 `ndf-relay` の設置・撤去・有無（#2461）。

入口は `<XDG_BIN_HOME か ~/.local/bin>/ndf-relay` に置く 2 行のシェルのスクリプトで、プラグインの `bin/ndf relay` を
呼ぶ。利用者が `ndf relay install` を打ったときだけ置く（黙って置かない）。撤去は、自分が置いた印の行がある入口
だけを消す（利用者が置いた同じ名前のファイルを消さない）。
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Mapping, NamedTuple

ENTRY_NAME = "ndf-relay"
MARK = "# relay の PATH の入口（ndf relay install が置いた）"


class Status(NamedTuple):
    """入口の有無。`state` は `absent`・`ours`（この導入先を指す）・`stale`（ほかの導入先を指す）・`foreign`（印が無い）。"""

    path: Path
    state: str


def bin_dir(environ: Mapping[str, str]) -> Path:
    value = environ.get("XDG_BIN_HOME")
    return Path(value) if value else Path.home() / ".local" / "bin"


def script(plugin_root: Path) -> str:
    target = shlex.quote(str(plugin_root / "bin" / "ndf"))
    return f"#!/bin/sh\n{MARK}\nexec {target} relay \"$@\"\n"


def status(plugin_root: Path, environ: Mapping[str, str]) -> Status:
    path = bin_dir(environ) / ENTRY_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:  # 読めない入口は無いものとして扱う
        return Status(path, "absent")
    if MARK not in text:
        return Status(path, "foreign")
    return Status(path, "ours" if text == script(plugin_root) else "stale")


def install(plugin_root: Path, environ: Mapping[str, str]) -> Status:
    """入口を置く。印の無い同じ名前のファイルがあれば置かない（`foreign` を返す）。"""
    now = status(plugin_root, environ)
    if now.state in ("ours", "foreign"):
        return now
    now.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = now.path.with_name(f".{ENTRY_NAME}.{os.getpid()}.tmp")
    tmp.write_text(script(plugin_root), encoding="utf-8")
    tmp.chmod(0o755)
    os.replace(tmp, now.path)
    return Status(now.path, "ours")


def uninstall(plugin_root: Path, environ: Mapping[str, str]) -> Status:
    """自分が置いた入口だけを消す。"""
    now = status(plugin_root, environ)
    if now.state in ("ours", "stale"):
        now.path.unlink(missing_ok=True)
        return Status(now.path, "absent")
    return now
