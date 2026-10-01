"""hook-trial の書き込み先の判定（ht_shparse.Targets）の現状固定テスト。

入口は write_targets(cmd, base)。期待値は今の出力を記録したもので、正しさは主張しない。
`@` は base、`^` は base の親を表す。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_bash")

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "experimental" / "hook-trial"))
import ht_shparse  # noqa: E402


@pytest.fixture
def targets(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    parent = os.path.realpath(tmp_path)
    base = parent + "/base"
    os.mkdir(base)

    def run(cmd: str, with_base: bool = True) -> list[str]:
        out = []
        for x in ht_shparse.write_targets(cmd, base if with_base else ""):
            if x.startswith(base):
                x = "@" + x[len(base) :]
            elif x.startswith(parent):
                x = "^" + x[len(parent) :]
            out.append(x)
        return out

    return run


# --- Targets.block: if / case / for / while の中の cd と書き込み先 ---

BLOCK = [
    ("if cd a; then echo x > f; fi; echo y > g", ["@/a/f"]),
    ("if true; then echo x > f; else echo y > g; fi; echo z > h", ["@/f", "@/g", "@/h"]),
    ("if true; then cd a; elif true; then echo x > f; else echo y > g; fi", []),
    ("if true; then cd a; echo x > f; fi; echo z > h", ["@/a/f"]),
    ("if true; then echo x > f & fi; echo z > h", ["@/f", "@/h"]),
    ("if true; then cd a & fi; echo z > h", ["@/h"]),
    ("case $x in a) cd d; echo 1 > f;; b) echo 2 > g;; esac; echo 3 > h", ["@/d/f", "@/g"]),
    ("case $x in a) cd d ;& b) echo 2 > g;; esac", ["@/d/g"]),
    ("case $x in a) cd d & echo 1 > f;; esac; echo 3 > h", ["@/f", "@/h"]),
    ("for i in 1 2; do echo $i > f; done; echo > g", ["@/f", "@/g"]),
    ("for i in $(echo a > s); do cd d; echo > f; done; echo > g", ["@/d/f"]),
    ("while read l; do echo $l > f; done < in > out", ["@/out", "@/f"]),
    ("while cd d; do echo > f; done; echo > g", ["@/d/f"]),
    ("until [ -f x ]; do echo > f; done", ["@/f"]),
    ("for ((i=0; i<3; i++)); do echo > f; done", ["@/f"]),
    ("for ((i=$(echo > s); i<3; i++)); do cd d; done; echo > g", ["@/s"]),
    ("while true; do if true; then cd d; fi; echo > f; done", []),
    # 現状の記録: case の値の位置にある置換の中の書き込み先（s）は拾われない
    ("case $(echo v > s) in a) echo 1 > f;; esac; echo 3 > h", ["@/f", "@/h"]),
]

BLOCK_NOBASE = [
    ("if true; then cd a; echo x > f; fi; echo z > h", ["f", "h"]),
    ("case $x in a) echo 1 > f;; esac", ["f"]),
]


@pytest.mark.parametrize("cmd,want", BLOCK)
def test_block(targets, cmd, want):
    assert targets(cmd) == want


@pytest.mark.parametrize("cmd,want", BLOCK_NOBASE)
def test_block_without_base(targets, cmd, want):
    assert targets(cmd, with_base=False) == want


# --- Targets.command: tee / sed -i / cp / mv の書き込み先と、cd による位置の移り ---

COMMAND = [
    ("echo x | tee a b", ["@/a", "@/b"]),
    ("tee -a a", ["@/a"]),
    ("sed -i s/a/b/ f", ["@/f"]),
    ("sed -n p f", []),
    ("sed -e s/a/b/ -i.bak f g", ["@/f", "@/g"]),
    ("sed --in-place=.b -f s.sed f", ["@/f"]),
    ("sed -ni s/a/b/ -- f", ["@/f"]),
    ("cp a b", ["@/b"]),
    ("mv -t d a b", ["@/d"]),
    ("cp --target-directory=d a", ["@/d"]),
    ("cp -td a", ["@/d"]),
    ("mv --target-directory d a", ["@/d"]),
    ("cd sub && echo > f", ["@/sub/f"]),
    ("cd sub; echo > f", ["@/sub/f"]),
    ("cd /abs/x; echo > f", ["/abs/x/f"]),
    ("cd; echo > f", []),
    ("cd $X; echo > f", []),
    ("cd ~; echo > f", []),
    ("cd -- d; echo > f", ["@/d/f"]),
    ("cd -; echo > f", []),
    ("cd -P d; echo > f", ["@/d/f"]),
    ("cd a b; echo > f", ["@/a/f"]),
    ("cd a/../b; echo > f", ["@/b/f"]),
    ("cd a; cd ../b; echo > f", ["@/b/f"]),
    ("cd a; cd $X; cd b; echo > f", []),
    ("command cd d; echo > f", ["@/d/f"]),
    ("builtin -- cd d; echo > f", ["@/d/f"]),
    ("time -p cd d; echo > f", ["@/d/f"]),
    ("command -p tee a", ["@/a"]),
    ("> f cd d; echo > g", ["@/f", "@/d/g"]),
    ("cd d > f; echo > g", ["@/f", "@/d/g"]),
    ("X=$(echo > s) echo hi", ["@/s"]),
    ("f() { cd d; }; f; echo > g", []),
    ("f() { echo > x; }; f; echo > g", ["@/x", "@/g"]),
    ("tee > f a", ["@/f", "@/a"]),
    ("cat <<< x tee a", ["@/a"]),
    ("env tee a | xargs sed -i s/a/b/ f", ["@/a", "@/f"]),
    # 現状の記録: 語の位置に関わらず tee を見るので、echo の引数の tee の後ろも書き込み先になる
    ("echo > f tee g", ["@/f", "@/g"]),
    # 現状の記録: 引数そのものが置換のとき、中の書き込み先（s・t）は拾われない
    ("echo $(echo > s) `echo > t`", []),
]

COMMAND_NOBASE = [
    ("cd d; echo > f; tee a", ["f", "a"]),
    ("f() { cd d; }; f; cp a b", ["b"]),
]


@pytest.mark.parametrize("cmd,want", COMMAND)
def test_command(targets, cmd, want):
    assert targets(cmd) == want


@pytest.mark.parametrize("cmd,want", COMMAND_NOBASE)
def test_command_without_base(targets, cmd, want):
    assert targets(cmd, with_base=False) == want
