"""スプリントのステージごとのプランをファイルへ書き、ステージの一覧を組む。"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from supervise_lib.paths import SELF


def _write_plans(wave: dict, i: int, out: Path) -> list[str]:
    """ステージのプランを 1 つずつファイルへ書き、パスの一覧を返す。"""
    paths = []
    for key, plan in wave["plans"].items():
        p = out / f"{i}-{key}.json"
        p.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
        paths.append(str(p))
    return paths


def _stage_entry(wave: dict, i: int, out: Path) -> dict:
    """ステージ 1 件の一覧の項目。then_of のステージは command を持たず、前のステージへ連結される。"""
    import parallel_lane

    entry = {"wave": i, "name": wave["name"]}
    if "gate" in wave:
        entry["gate"] = wave["gate"]
    elif "manual" in wave:
        entry.update(manual=wave["manual"], note=wave["note"])
    else:
        paths = _write_plans(wave, i, out)
        entry["plans"] = paths
        if "note" in wave:
            entry["note"] = wave["note"]
        if "then_of" in wave:
            entry["then_of"] = wave["then_of"]
        else:
            entry["done"] = str(out.resolve() / f"done-{i}.json")  # 待ちとキューの続きが読む（#1426 の決定 6）
            entry["command"] = (
                f"python3 {shlex.quote(str(SELF))} queue "
                + " ".join(map(shlex.quote, paths))
                + f" --max {parallel_lane.queue_max()} --done {shlex.quote(entry['done'])}"
            )
    return entry


def write_stage_index(waves: list[dict], out: Path) -> list[dict]:
    """ステージごとのプランをファイルへ書き、ステージの一覧（command / then_of の連結を含む）を返す。"""
    index = []
    for i, wave in enumerate(waves, 1):
        entry = _stage_entry(wave, i, out)
        if "then_of" in entry:
            # 前のステージの queue が --then で続けて流す
            prev = next(e for e in index if e["name"] == entry["then_of"])
            prev["command"] += " --then " + " ".join(map(shlex.quote, entry["plans"]))
        index.append(entry)
    return index
