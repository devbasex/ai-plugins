from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ndf import slot_pool

OWNER = {"pid": os.getpid()}
SRC_DIR = Path(__file__).resolve().parents[2] / "src"
CHILD_SECONDS = 30
# 枠を取って番号を出し、返さずに終わる子
TAKE_AND_EXIT = (
    "import os, sys; from pathlib import Path; from ndf import slot_pool; "
    "taken = slot_pool.take(Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), {'pid': os.getpid()}); "
    "print(taken.index)"
)


def test_weights_add_up_to_the_capacity(tmp_path):
    first = slot_pool.take(tmp_path, 1024, 512, OWNER)
    second = slot_pool.take(tmp_path, 1024, 512, OWNER)
    assert first is not None and second is not None and first.index != second.index
    assert slot_pool.take(tmp_path, 1024, 1, OWNER) is None
    assert slot_pool.held_mib(tmp_path) == 1024
    first.release()
    third = slot_pool.take(tmp_path, 1024, 512, OWNER)
    assert third is not None and third.index == first.index
    second.release()
    third.release()
    assert slot_pool.held_mib(tmp_path) == 0


def test_an_empty_pool_takes_a_launch_heavier_than_the_capacity(tmp_path):
    taken = slot_pool.take(tmp_path, 1024, 4096, OWNER)
    assert taken is not None
    assert slot_pool.take(tmp_path, 1024, 1, OWNER) is None
    taken.release()


def test_owner_description_keeps_the_weight(tmp_path):
    taken = slot_pool.take(tmp_path, 1024, 256, OWNER)
    taken.describe({"started_at": 1.0})
    assert json.loads(slot_pool.slot_path(tmp_path, taken.index, ".json").read_text()) == {"started_at": 1.0, "weight_mib": 256}
    taken.release()


def _take_in_a_gone_owner(directory: Path, capacity: int, weight: int) -> int:
    """子プロセスで枠を取り、返さずに終わらせる（記述を残して持ち主が消えた形）。取った番号を返す。"""
    env = {**os.environ, "PYTHONPATH": str(SRC_DIR)}
    done = subprocess.run(
        [sys.executable, "-c", TAKE_AND_EXIT, str(directory), str(capacity), str(weight)],
        env=env,
        capture_output=True,
        text=True,
        timeout=CHILD_SECONDS,  # 時間切れなら run が子を止めて TimeoutExpired を送る
        check=True,
    )
    return int(done.stdout)


def test_an_owner_whose_lock_is_free_is_not_counted(tmp_path):
    # 持ち主のプロセスが記述を残して消えるとカーネルがロックを解く
    index = _take_in_a_gone_owner(tmp_path, 1024, 1024)
    assert slot_pool.slot_path(tmp_path, index, ".json").exists()
    assert slot_pool.held_mib(tmp_path) == 0
    again = slot_pool.take(tmp_path, 1024, 1024, OWNER)
    assert again is not None and again.index == index
    again.release()


def test_weights_come_from_the_limits_declaration():
    assert slot_pool.weight_mib("llm-cli") < slot_pool.weight_mib("test")


def test_write_json_writes_one_line_of_unicode(tmp_path):
    # 現状固定: 日本語を逃がさず 1 行で書き、末尾に改行を 1 つ置く。一時ファイルは残らない
    path = tmp_path / "owner.json"
    slot_pool.write_json(path, {"worktree": "作業"})
    assert path.read_bytes() == '{"worktree": "作業"}\n'.encode()
    assert [p.name for p in tmp_path.iterdir()] == ["owner.json"]


def test_a_failed_replace_keeps_the_old_file_and_raises(tmp_path, monkeypatch):
    # 現状固定: 置き換えに失敗すると例外をそのまま送り、元のファイルは書き換えない
    path = tmp_path / "events.jsonl"
    path.write_text("old\n", encoding="utf-8")

    def refuse(src, dst):
        raise OSError("置き換えられない")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="置き換えられない"):
        slot_pool.replace_text(path, "new\n")
    assert path.read_text(encoding="utf-8") == "old\n"
