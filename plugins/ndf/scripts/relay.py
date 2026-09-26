#!/usr/bin/env python3
"""NDF のラッパー relay.py のランチャー（#895・#1142 の C6）。

副命令: `run` / `stop` / `mark` / `install` / `uninstall` / `status` / `startup` / `question open|close` /
`is-child` / `notice`。役割の表と中身は `relay_lib/` にある（表は relay_lib/__init__.py）。プラグインの `scripts/relay.py` と、
`/ndf:install-wrapper` が `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/relay.py` へ置く複製は同じバイト列である。
ランチャーは使う `relay_lib/` を次の順に 1 つ選んで `sys.path` の先頭に置き、`relay_lib.main` を呼ぶ:

1. 隣の `relay_lib/`（プラグインのキャッシュ）
2. 隣の `relay.current` が指すバージョンディレクトリ（複製）

どちらも無ければ理由を 1 行出し、`run` なら本物の claude をそのまま起動する。標準ライブラリだけを使う。
規約は skills/development-workflow/references/relay.md にある。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
CURRENT = "relay.current"


def package_root(here: str = HERE) -> str | None:
    """`relay_lib/` を持つディレクトリ。見つからなければ None。"""
    if os.path.isfile(os.path.join(here, "relay_lib", "__init__.py")):
        return here
    try:
        with open(os.path.join(here, CURRENT)) as f:
            name = f.read().strip()
    except OSError:
        return None
    root = os.path.join(here, name)
    if name and os.sep not in name and os.path.isfile(os.path.join(root, "relay_lib", "__init__.py")):
        return root
    return None


def real_claude() -> str | None:
    """PATH の `claude` のうち、ラッパーを呼ぶもの（先頭に relay.py の名前を持つもの）でない最初の 1 つ。"""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d or ".", "claude")
        if not (os.path.isfile(p) and os.access(p, os.X_OK)):
            continue
        try:
            with open(p, "rb") as f:
                if b"relay.py" in f.read(4096):
                    continue
        except OSError:
            continue
        return os.path.abspath(p)
    return None


def missing(argv: list[str]) -> int:
    sys.stderr.write(f"ndf-relay: ラッパーの中身（relay_lib）が {HERE} に見つからない。"
                     "/ndf:install-wrapper を打ち直す\n")
    if argv[:1] == ["run"] and os.environ.get("NDF_RELAY_DEPTH", "0") in ("", "0"):
        claude = real_claude()
        if claude:
            env = dict(os.environ, NDF_RELAY_DEPTH="1")
            os.execve(claude, [claude] + argv[1:], env)
        return 127
    return 1


ROOT = package_root()
if ROOT is None:
    main = missing
else:
    if ROOT in sys.path:
        sys.path.remove(ROOT)
    sys.path.insert(0, ROOT)
    import relay_lib  # noqa: E402

    main = relay_lib.main

MODULES = ("common", "proc", "record", "mark", "claude", "terminal", "run", "shellrc", "version_dir", "install")


def __getattr__(name: str):
    """移行の間、ランチャーを読み込んで `relay.<名前>` で読む呼び出し元のため、relay_lib の定義を返す（読むだけ）。"""
    for mod in MODULES:
        m = sys.modules.get(f"relay_lib.{mod}")
        if m is not None and hasattr(m, name):
            return getattr(m, name)
    raise AttributeError(name)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
