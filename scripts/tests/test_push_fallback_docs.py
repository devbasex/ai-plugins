"""push の退避の値を持つのは共通層（`plugins/ndf/scripts/lib/git-credential.sh`）1 か所であること（#524）。"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "plugins/ndf/scripts/lib/git-credential.sh"


def fallback_args() -> list[str]:
    out = subprocess.run(
        ["bash", "-c", f'. "{LIB}"; ndf_git_credential_fallback_args'],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line for line in out.split("\n") if line]


def test_the_shared_library_is_the_only_source_of_the_value():
    assert LIB.is_file()
    assert fallback_args() == [
        "-c", "credential.helper=",
        "-c", "credential.helper=!gh auth git-credential",
    ]


def test_the_shared_library_holds_the_value_and_nothing_else():
    """共通層が定義するのは値を出す関数だけであること。

    **呼び出し側の無いシェル関数を置くと、退避の分岐がシェルと実装の 2 か所に分かれる。**
    失敗→退避→1 度だけ再試行という振る舞いを持つのは退避する側（`gitfacts.py`）で、
    共通層は値だけを持つ。
    """
    names = re.findall(
        r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", LIB.read_text(encoding="utf-8"), re.M
    )
    assert names == ["ndf_git_credential_fallback_args"], (
        f"共通層が値の他に振る舞いを持っている: {names}")
