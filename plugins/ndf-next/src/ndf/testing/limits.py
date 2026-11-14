"""テストの枠の上限の値（#2711 の契約「設定 `.ndf/resources.json`」・決定 5）。

設定とテストの宣言は、呼ばれた位置から根へ向かって最初に見つかる `.ndf/` から読む
（worktree はメインディレクトリの下に置くので、worktree に無ければメインディレクトリの値が効く）。
設定を書き換えるコマンドは作らない。
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ndf import slot_pool
from ndf.testing import machine, weight

RESOURCES_FILE = "resources.json"
PROJECT_FILE = "project.json"
SECTION = "test_slots"
# 1 本の全体テストが使う CPU の数（`-n 4`。決定 5）。メモリの見込みは重い起動の枠のテストの重み
CPUS_PER_RUN = 4
# テスト 1 本の重みの種類（`contracts/limits.json` の `launch.weight.test`）
TEST_WEIGHT = "test"
# メモリの下限の最小と、全体のメモリに対する割合（earlyoom の既定に揃える）
FLOOR_MIN_MIB = 2 * 1024
FLOOR_RATIO = 0.10
# 待ちの上限の既定（全体テストの上限と同じ）
DEFAULT_WAIT_LIMIT_SECONDS = 1800


class SlotConfigError(Exception):
    """設定の値が誤っている。`items` に直す箇所を持つ。"""

    def __init__(self, message: str, items: list[str]):
        super().__init__(message)
        self.items = items


@dataclass(frozen=True)
class Limits:
    max_runs: int
    memory_floor_mib: int
    wait_limit_seconds: int

    @property
    def capacity_mib(self) -> int:
        """重い起動の枠の容量。テスト `max_runs` 本の重みの合計で、LLM の CLI もこの中で数える（#3368）。"""
        return self.max_runs * slot_pool.weight_mib(TEST_WEIGHT)


# 設定の鍵（Limits のフィールドの名前と同じ）
_LIMIT_KEYS = tuple(f.name for f in dataclasses.fields(Limits))


def default_limits(cpus: int, total_mib: int | None) -> Limits:
    """CPU の数と全体のメモリから既定の上限を決める。全体のメモリが読めなければ CPU だけで決める。"""
    by_cpu = cpus // CPUS_PER_RUN
    by_memory = by_cpu if total_mib is None else total_mib // slot_pool.weight_mib(TEST_WEIGHT)
    floor = FLOOR_MIN_MIB if total_mib is None else max(FLOOR_MIN_MIB, int(total_mib * FLOOR_RATIO))
    return Limits(max(1, min(by_cpu, by_memory)), floor, DEFAULT_WAIT_LIMIT_SECONDS)


def find_setting(cwd: Path, name: str) -> Path | None:
    """`cwd` から根へ向かって `.ndf/<name>` を探す。"""
    for base in (cwd.resolve(), *cwd.resolve().parents):
        candidate = base / ".ndf" / name
        if candidate.is_file():
            return candidate
    return None


def _positive(section: Mapping[str, object], key: str, path: Path, errors: list[str]) -> int | None:
    if key not in section:
        return None
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        errors.append(f"{path}: {SECTION}.{key} は 1 以上の整数にする（今の値: {value!r}）")
        return None
    return value


def load_limits(cwd: Path, *, cpus: int | None = None, total_mib: int | None = None) -> Limits:
    """設定と既定から上限を決める。誤った値は既定へ落とさず `SlotConfigError` を送る。"""
    base = default_limits(machine.cpu_count() if cpus is None else cpus, machine.total_mib() if total_mib is None else total_mib)
    path = find_setting(cwd, RESOURCES_FILE)
    if path is None:
        return base
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SlotConfigError(f"設定を読めない: {path}", [f"{path}: JSON として読めない（{exc}）"]) from exc
    if isinstance(data, dict) and SECTION not in data:
        return base
    section = data[SECTION] if isinstance(data, dict) else None
    if not isinstance(section, dict):
        raise SlotConfigError(f"設定の形が違う: {path}", [f"{path}: {SECTION} はオブジェクトにする"])
    errors: list[str] = []
    values = {key: _positive(section, key, path, errors) for key in _LIMIT_KEYS}
    if errors:
        raise SlotConfigError(f"設定の値が誤っている: {path}", errors)
    return dataclasses.replace(base, **{key: value for key, value in values.items() if value is not None})


def load_suites(cwd: Path) -> list[dict[str, object]]:
    """宣言（`.ndf/project.json` の `test.suites`）のうちテストの suite。読めなければ空の一覧。"""
    path = find_setting(cwd, PROJECT_FILE)
    if path is None:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    test = data.get("test") if isinstance(data, dict) else None
    suites = test.get("suites") if isinstance(test, dict) else None
    if not isinstance(suites, list):
        return []
    return [s for s in suites if isinstance(s, dict) and weight.is_test_suite(s)]
