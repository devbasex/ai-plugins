"""hook スクリプトが古い bash でも動くことを検査する。

macOS が標準で持つ bash は 3.2 である。`mapfile` / `readarray` / 連想配列 /
`${var,,}` はいずれも bash 4 以降の機能で、3.2 では 127 や構文エラーになる。
hook は失敗しても黙って終わるため、案内が出ない形で壊れる。
"""
from __future__ import annotations

import re

import pytest

from worktree_helpers import SCRIPTS_DIR

# 対象は hook から呼ばれるスクリプトと、Skill が呼ぶシェルスクリプト全体。
# 配布先の環境は選べないため、bash 3.2 で動くことを前提に置く。
_PLUGIN_ROOT = SCRIPTS_DIR.parent
TARGETS = sorted(
    set(_PLUGIN_ROOT.glob("scripts/*.sh"))
    | set(_PLUGIN_ROOT.glob("scripts/lib/*.sh"))
    | set(_PLUGIN_ROOT.glob("skills/*/scripts/*.sh"))
    | set(_PLUGIN_ROOT.glob("skills/*/scripts/lib/*.sh"))
)

# 行頭から見て、コメントではない箇所に現れるものだけを対象にする。
BASH4_ONLY = {
    "mapfile": re.compile(r"^\s*mapfile\b"),
    "readarray": re.compile(r"^\s*readarray\b"),
    "連想配列": re.compile(r"^\s*(local|declare)\s+-A\b"),
    "小文字化の展開": re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*,,\}"),
    "大文字化の展開": re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\^\^\}"),
}


@pytest.mark.parametrize("script", TARGETS, ids=lambda p: p.name)
def test_no_bash4_only_features(script) -> None:
    found = []
    for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for label, pattern in BASH4_ONLY.items():
            if pattern.search(line):
                found.append(f"{script.name}:{number} {label}: {line.strip()}")
    assert not found, "\n".join(found)


@pytest.mark.parametrize("script", TARGETS, ids=lambda p: p.name)
def test_scripts_parse(script) -> None:
    import subprocess

    proc = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
