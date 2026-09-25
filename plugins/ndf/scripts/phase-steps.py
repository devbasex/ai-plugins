#!/usr/bin/env python3
"""phase-steps.py: 決まった手順のステップの互換の入口（#827 の試作。#846 から各 Skill のスクリプトへ移した）。

各サブコマンドを移した先のスクリプトへそのまま渡す。引数・出力・終了コードは移した先と同じ。
`--root` はサブコマンドの前でも後でも受ける（前に書いたものは移した先へ後ろに付けて渡す）。

    cleanup                                  → merged-steps.py
    spec-finalize                            → plan-to-spec-steps.py
    bump / changelog / release / approval-facts → release-steps.py
    verify-install                           → release-verification-steps.py

結果の形と終了コードは lib/README.md の「手順のスクリプトの結果」にある。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUTES = {
    "cleanup": "merged-steps.py",
    "spec-finalize": "plan-to-spec-steps.py",
    "bump": "release-steps.py",
    "changelog": "release-steps.py",
    "release": "release-steps.py",
    "approval-facts": "release-steps.py",
    "verify-install": "release-verification-steps.py",
}
USAGE = "usage: phase-steps.py [--root ROOT] {" + ",".join(ROUTES) + "} ..."


def split(argv):
    """(サブコマンド, 残りの引数, 前に書いた --root の値) を返す。誤りは (None, 理由, None)。"""
    root = None
    i = 0
    while i < len(argv):
        t = argv[i]
        if t == "--root":
            if i + 1 >= len(argv):
                return None, "--root に値が無い", None
            root = argv[i + 1]
            i += 2
            continue
        if t.startswith("--root="):
            root = t[len("--root="):]
            i += 1
            continue
        if t in ROUTES:
            return t, argv[i + 1:], root
        return None, f"知らない引数かサブコマンド: {t}", None
    return None, "サブコマンドが無い", None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(USAGE)
        print(__doc__)
        return 0
    sub, rest, root = split(argv)
    if sub is None:
        print(USAGE, file=sys.stderr)
        print(f"phase-steps.py: error: {rest}", file=sys.stderr)
        return 2
    if root is not None:
        rest = [*rest, "--root", root]
    return subprocess.run([sys.executable, str(HERE / ROUTES[sub]), sub, *rest]).returncode


if __name__ == "__main__":
    sys.exit(main())
