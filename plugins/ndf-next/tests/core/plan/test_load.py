"""プランの実行の負荷（B3・B6）。20 本のプランの実行と 14 本のラッパーをそろえてから流し、各プランの 5 ステップの回を 3 回流す。

1 本のテストで B3 と B6 をまとめて確かめる（`-n auto` でも 1 つのワーカーで 1 回だけ流れ、`xdist_group` で
`--dist loadgroup` のときも負荷の組を 1 つのワーカーへ寄せる）。合格の条件は他のテストとの CPU の取り合いに左右されない
（詰まりの回数・ステップの回数・WAL の大きさ・働いた回数だけを見て、所要の秒は見ない）。測った値は JSON にして
警告 `LoadReport` で出す。警告は xdist のワーカーからも pytest の終わりの要約へ届き、通ったときも CI のログに残る
（PR に貼る値）。本数は `NDF_PLAN_LOAD_PLANS`・`NDF_PLAN_LOAD_WRAPPERS` で減らせる（手元で小さく流すとき）。
"""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path

import pytest

from . import _plan_load as L

PLANS = int(os.environ.get("NDF_PLAN_LOAD_PLANS", "20"))
WRAPPERS = int(os.environ.get("NDF_PLAN_LOAD_WRAPPERS", "14"))
ROUNDS = 3


class LoadReport(UserWarning):
    """負荷の測定の値（PR に貼る）。"""


@pytest.mark.xdist_group("plan-load")
def test_plans_and_wrappers_run_rounds_without_lock_errors(tmp_path: Path) -> None:
    out = L.run(tmp_path, PLANS, WRAPPERS, ROUNDS)
    text = json.dumps(out, ensure_ascii=False, sort_keys=True)
    (tmp_path / "plan-load.json").write_text(text + "\n", encoding="utf-8")
    warnings.warn(LoadReport(f"plan-load: {text}"), stacklevel=1)

    # 回し終えた: 各プランの各回の 5 ステップがすべて流れ、どれも 2 回流れていない
    assert out["calls_by_status"].get("next:ok", 0) >= PLANS * ROUNDS, text
    assert out["steps_run"] == PLANS * ROUNDS * L.STEPS_PER_PLAN, text
    assert out["steps_run_twice"] == 0, text
    # B3: 詰まりが無い。1 ステップあたりの上乗せ（`overhead_per_step_seconds`）はマシンの性能で決まるので出すだけにする
    assert out["database_is_locked"] == 0, text
    # B6: WAL が上限以下。quick_check は鍵のファイルごとに高々 1 回働き、片付けの走査は目印で 1 回だけ働く
    assert out["wal_max_bytes"] <= L.WAL_LIMIT_BYTES, text
    assert out["quick_check"]["count"] <= out["key_files"], text
    assert out["quick_check"]["count"] < out["calls"], text
    assert out["sweep"]["count"] == 1, text
    assert out["sweep"]["called"] > out["sweep"]["count"], text
