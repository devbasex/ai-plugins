from __future__ import annotations

import json
import os

from ndf import slot_pool

OWNER = {"pid": os.getpid()}


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


def test_an_owner_whose_lock_is_free_is_not_counted(tmp_path):
    # 持ち主のプロセスが記述を残して消えるとカーネルがロックを解く（解ける所は testing の test_slots が確かめる）
    taken = slot_pool.take(tmp_path, 1024, 1024, OWNER)
    taken._lock.release()
    assert slot_pool.slot_path(tmp_path, taken.index, ".json").exists()
    assert slot_pool.held_mib(tmp_path) == 0
    again = slot_pool.take(tmp_path, 1024, 1024, OWNER)
    assert again is not None and again.index == taken.index
    again.release()


def test_weights_come_from_the_limits_declaration():
    assert slot_pool.weight_mib("llm-cli") < slot_pool.weight_mib("test")
