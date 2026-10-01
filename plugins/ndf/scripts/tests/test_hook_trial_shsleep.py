"""hook-trial の前景の sleep の判定（ht_shsleep._sleep_cmd）の現状固定テスト。

入口は sleep_deny(cmd, limit)。期待値は上限 30 秒での今の出力を記録したもので、正しさは主張しない。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_bash")

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "experimental" / "hook-trial"))
import ht_shsleep  # noqa: E402

SLEEP = [
    # 秒数と単位
    ("sleep 5", False),
    ("sleep 30", False),
    ("sleep 31", True),
    ("sleep 1m", True),
    ("sleep 0.5m", False),
    ("sleep 1h", True),
    ("sleep 1d", True),
    ("sleep", False),
    ("sleep $X", False),
    ("sleep infinity", False),
    ("sleep 10 30", False),
    # 背景と並び
    ("sleep 60 &", False),
    ("(sleep 60) &", False),
    ("sleep 60 & wait", False),
    ("echo a; sleep 60", True),
    ("true && sleep 60", True),
    ("echo sleep 60", False),
    ("echo a | sleep 60", True),
    # 前に付く命令
    ("command sleep 60", True),
    ("exec sleep 60", True),
    ("nohup sleep 60", True),
    ("env sleep 60", True),
    ("env X=1 Y=2 sleep 60", True),
    ("nice -n 5 sleep 60", True),
    ("timeout 10 sleep 60", True),
    ("timeout -k 5 10s sleep 60", True),
    ("time sleep 60", True),
    ("time -p sleep 60", True),
    ("nohup nice env sleep 60", True),
    ("command", False),
    ("env X=1", False),
    ("timeout 10", False),
    # シェルの -c
    ("bash -c 'sleep 60'", True),
    ("sh -c 'sleep 5'", False),
    ("zsh -c 'sleep 60'", True),
    ("dash -ec 'sleep 60'", True),
    ("bash -lc 'sleep 60'", True),
    ("bash -o pipefail -c 'sleep 60'", True),
    ("bash +o posix -c 'sleep 60'", True),
    ("bash -eo pipefail -c 'sleep 60'", True),
    ("bash --norc -c 'sleep 60'", True),
    ("bash --command 'sleep 60'", False),
    ("bash -c", False),
    ("bash script.sh", False),
    ("bash -x script.sh -c 'sleep 60'", False),
    ("bash -c 'sleep 60 &'", False),
    ("bash -c \"bash -c 'sleep 60'\"", True),
    # eval
    ("eval sleep 60", True),
    ("eval 'sleep 60'", True),
    ('eval "sleep 5"', False),
    ("eval", False),
    ("eval sleep 60 '&'", False),
    ("eval eval eval eval eval sleep 60", True),
    ("eval eval eval eval eval eval sleep 60", False),  # 深さ 5 を超えたら見ない
    # 置換の中
    ("echo $(sleep 60)", True),
    ("echo `sleep 60`", True),
    ("X=$(sleep 60) echo", True),
    ('echo "a $(sleep 60) b"', True),
    ("cat <(sleep 60)", True),
    ("echo $(sleep 60 &)", False),
    ("echo > $(sleep 60)", True),
    ("cat <<EOF\n$(sleep 60)\nEOF", False),
    ("echo $(echo $(sleep 60))", True),
    # ループの中
    ("while true; do sleep 1; done", True),
    ("while sleep 1; do echo; done", False),
    ("while true; do bash -c 'sleep 1'; done", True),
    ("while true; do eval sleep 1; done", True),
    ("while true; do sleep; done", False),
    ("while true; do sleep 1 & done", False),
    ("until false; do sleep 1; done", True),
    ("for i in 1 2; do sleep 1; done", False),
    ("for i in 1 2; do sleep 60; done", True),
    # 対象でない命令
    ("python3 -c 'sleep 60'", False),
    ("xargs sleep 60", False),
    ("sudo sleep 60", False),
    ("# sleep 60", False),
    ("", False),
]


@pytest.mark.parametrize("cmd,want", SLEEP)
def test_sleep_deny(cmd, want):
    assert ht_shsleep.sleep_deny(cmd, 30) is want


def test_sleep_deny_arguments():
    assert ht_shsleep.sleep_deny("sleep 5", 30, in_loop=True) is True
    assert ht_shsleep.sleep_deny("bash -c 'sleep 5'", 30, in_loop=True) is True
    assert ht_shsleep.sleep_deny("sleep 60", 30, depth=6) is False
    assert ht_shsleep.sleep_deny("sleep 30", 29.5) is True
