"""中継の複製 `~/.claude/ndf/relay.py` と PATH のエントリポイント `ndf-relay` の設置・撤去・有無（#2461・#2583・要求の 346 行）。

- 複製（`<CLAUDE_CONFIG_DIR か ~/.claude>/ndf/relay.py`）は標準ライブラリだけの小さな起動の部品で、隣の
  `relay.root`（プラグインの根のパス 1 行）が指すプラグインの `bin/ndf relay` を呼ぶ。中身は版によらず同じで、
  プラグインを更新しても `relay.root` を書き換えるだけで済む（1 回の導入で動き続ける）
- プラグインの更新で `relay.root` の指す版の置き場が消えたら、複製は同じ親の下の最も新しい版の置き場へ向け直す。
  見つからなければ理由と打ち直しのコマンドを 1 行出して止まる（何も出さずに失敗しない。#2583）
- PATH のエントリポイントは `<XDG_BIN_HOME か ~/.local/bin>/ndf-relay` に置く 2 行のシェルのスクリプトで、複製を呼ぶ
- どれも利用者が `ndf relay install` を打ったときだけ置く（黙って置かない）。撤去は、自分が置いた印の行がある
  ファイルだけを消す（利用者が置いた同じ名前のファイルを消さない）
- 旧い NDF（10.x）の複製は印を持たない。旧いラッパーが動いている間は置き換えない（3341-2）
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Mapping, NamedTuple

from ndf import identity
from ndf.domains.relay import signals

ENTRY_NAME = "ndf-relay"
COPY_NAME = "relay.py"
ROOT_FILE = "relay.root"
MARK = "# 中継の複製（ndf relay install が置いた）"
ENTRY_MARK = "# 中継の PATH のエントリポイント（ndf relay install が置いた）"

_LAUNCHER = """#!/usr/bin/env python3
{mark}
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
POINTER = HERE / "{root_file}"


def _usable(root):
    return root is not None and os.access(root / "bin" / "{entry}", os.X_OK)


def _newest(parent):
    found = [p for p in parent.iterdir() if _usable(p)] if parent.is_dir() else []
    return max(found, key=lambda p: (p / "bin" / "{entry}").stat().st_mtime) if found else None


def main():
    text = POINTER.read_text(encoding="utf-8").strip() if POINTER.is_file() else ""
    root = Path(text) if text else None
    if not _usable(root) and root is not None:
        root = _newest(root.parent)
        if root is not None:
            POINTER.write_text(str(root) + "\\n", encoding="utf-8")
    if not _usable(root):
        print("ndf-relay: 中継を呼ぶプラグインが見つからない（" + str(POINTER) + "）。"
              "プラグインの bin/{entry} relay install で置き直す", file=sys.stderr)
        return {code}
    ndf = str(root / "bin" / "{entry}")
    os.execv(ndf, [ndf, "relay", *sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
"""


class Status(NamedTuple):
    """置いたファイルの有無。`state` は `absent`・`ours`・`stale`（中身が違う）・`foreign`（印が無い）。"""

    path: Path
    state: str


class Installed(NamedTuple):
    """設置の結果。`copy`・`entry` はそれぞれの状態、`on_path` はエントリポイントの置き場が PATH にあるか。"""

    copy: Status
    entry: Status
    on_path: bool


def config_dir(environ: Mapping[str, str]) -> Path:
    return signals.claude_config_root(environ) / identity.FINAL_NAME


def bin_dir(environ: Mapping[str, str]) -> Path:
    value = environ.get("XDG_BIN_HOME")
    return Path(value) if value else Path.home() / ".local" / "bin"


def launcher(missing_code: int) -> str:
    return _LAUNCHER.format(mark=MARK, root_file=ROOT_FILE, code=missing_code, entry=identity.FIXED_WORDS["entrypoint"])


def entry_script(copy: Path) -> str:
    return f'#!/bin/sh\n{ENTRY_MARK}\nexec python3 {shlex.quote(str(copy))} "$@"\n'


def _status(path: Path, mark: str, body: str) -> Status:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:  # 無いファイルは置いていない
        return Status(path, "absent")
    if mark not in text:
        return Status(path, "foreign")
    return Status(path, "ours" if text == body else "stale")


def status(environ: Mapping[str, str], missing_code: int) -> Installed:
    copy = config_dir(environ) / COPY_NAME
    entry = bin_dir(environ) / ENTRY_NAME
    paths = environ.get("PATH")
    on_path = str(entry.parent) in (paths.split(os.pathsep) if paths else [])
    return Installed(
        _status(copy, MARK, launcher(missing_code)),
        _status(entry, ENTRY_MARK, entry_script(copy)),
        on_path,
    )


def install(environ: Mapping[str, str], plugin_root: Path, missing_code: int, replace_foreign: bool) -> Installed:
    """複製とエントリポイントを置き、`relay.root` をこのプラグインへ向ける。

    印の無い複製（旧い NDF の複製）は `replace_foreign` が真のときだけ置き換える。印の無いエントリポイントは置き換えない。
    """
    now = status(environ, missing_code)
    copy = now.copy
    if copy.state != "foreign" or replace_foreign:
        signals.replace_text(copy.path, launcher(missing_code), 0o755)
        signals.replace_text(copy.path.parent / ROOT_FILE, f"{plugin_root}\n", 0o644)
        copy = Status(copy.path, "ours")
    entry = now.entry
    if entry.state in ("absent", "stale"):
        signals.replace_text(entry.path, entry_script(copy.path), 0o755)
        entry = Status(entry.path, "ours")
    return Installed(copy, entry, now.on_path)


def uninstall(environ: Mapping[str, str], missing_code: int) -> Installed:
    """自分が置いた複製・向き先・エントリポイントだけを消す。"""
    now = status(environ, missing_code)
    out = []
    for item in (now.copy, now.entry):
        if item.state in ("ours", "stale"):
            item.path.unlink(missing_ok=True)
            item = Status(item.path, "absent")
        out.append(item)
    if out[0].state == "absent":
        (out[0].path.parent / ROOT_FILE).unlink(missing_ok=True)
    return Installed(out[0], out[1], now.on_path)
